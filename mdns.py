"""Répondeur mDNS minimal : annonce des noms « .local » supplémentaires (sous-domaines) sur le
réseau local, sans rien installer. Bibliothèque standard uniquement.

Le service système du Pi (avahi) n'annonce que le nom de la machine (pi-infirmerie.local).
Ce répondeur répond aux requêtes pour les noms de config.json (admin.pi-infirmerie.local,
stats.pi-infirmerie.local...) avec l'adresse IP du Pi. Il cohabite avec avahi sur le port 5353.

Limites : IPv4 seulement ; ne vérifie pas les conflits de noms ; ne sert que les noms demandés
(il ne répond jamais pour un autre nom).
"""
import socket
import struct
import threading
import time

MCAST, PORT, TTL = "224.0.0.251", 5353, 120
TYPE_A, TYPE_AAAA, TYPE_ANY, CLASS_IN, FLUSH = 1, 28, 255, 1, 0x8000


def local_ip():
    """Adresse IPv4 utilisée pour sortir du Pi (aucun paquet n'est envoyé)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    finally:
        s.close()


def encode_name(name):
    return b"".join(bytes([len(p)]) + p.encode("ascii") for p in name.strip(".").split(".")) + b"\0"


def decode_name(data, off):
    """Nom DNS (avec compression) lu à partir de off ; retourne (nom en minuscules, offset suivant)."""
    labels, end, hops = [], None, 0
    while True:
        n = data[off]
        if n == 0:
            off += 1
            break
        if n & 0xC0 == 0xC0:
            if end is None:
                end = off + 2
            off, hops = ((n & 0x3F) << 8) | data[off + 1], hops + 1
            if hops > 10:
                raise ValueError("compression invalide")
            continue
        labels.append(data[off + 1:off + 1 + n].decode("ascii", "ignore").lower())
        off += 1 + n
    return ".".join(labels), (end if end is not None else off)


def parse_questions(data):
    """Questions d'une requête : [(nom, type)] ; liste vide si ce n'est pas une requête."""
    qid, flags, qd = struct.unpack(">HHH", data[:6])
    if flags & 0x8000 or qd == 0:                      # c'est une réponse, ou pas de question
        return qid, []
    off, out = 12, []
    for _ in range(qd):
        name, off = decode_name(data, off)
        qtype, _qclass = struct.unpack(">HH", data[off:off + 4])
        off += 4
        out.append((name, qtype))
    return qid, out


def answer_for(name, qtype, ip):
    """Enregistrement de réponse. A : l'adresse. AAAA : « il n'existe pas » (NSEC), pour que les
    clients n'attendent pas inutilement une adresse IPv6."""
    owner = encode_name(name)
    if qtype in (TYPE_A, TYPE_ANY):
        return owner + struct.pack(">HHIH", TYPE_A, CLASS_IN | FLUSH, TTL, 4) + socket.inet_aton(ip)
    if qtype == TYPE_AAAA:
        rdata = owner + b"\x00\x01\x40"                # bitmap : seul le type A existe
        return owner + struct.pack(">HHIH", 47, CLASS_IN | FLUSH, TTL, len(rdata)) + rdata
    return None


def build_response(qid, answers, questions=None):
    head = struct.pack(">HHHHHH", qid, 0x8400, len(questions or []), len(answers), 0, 0)
    body = b"".join(encode_name(n) + struct.pack(">HH", t, CLASS_IN) for n, t in (questions or []))
    return head + body + b"".join(answers)


class Responder(threading.Thread):
    def __init__(self, names, log=print, shared=()):
        super().__init__(daemon=True)
        self.names = {n.lower().strip(".") for n in names}
        self.shared = {n.lower().strip(".") for n in shared}   # noms aussi annoncés par avahi : IPv4 seulement
        self.log = log
        self.ip = local_ip()

    def _socket(self):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        s.bind(("", PORT))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton(MCAST) + socket.inet_aton(self.ip))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(self.ip))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        s.settimeout(2)
        return s

    def announce(self, s):
        recs = [answer_for(n, TYPE_A, self.ip) for n in sorted(self.names)]
        s.sendto(build_response(0, recs), (MCAST, PORT))

    def run(self):
        try:
            s = self._socket()
        except OSError as e:
            return self.log(f"[mdns] indisponible ({e}) : les noms {', '.join(sorted(self.names))} ne seront pas annoncés")
        self.log(f"[mdns] annonce {', '.join(sorted(self.names))} -> {self.ip}")
        for _ in range(2):                              # annonce au démarrage
            self.announce(s)
            time.sleep(1)
        last = time.time()
        while True:
            try:
                data, addr = s.recvfrom(2048)
                qid, questions = parse_questions(data)
            except socket.timeout:
                questions, addr, qid = [], None, 0
            except (OSError, ValueError, IndexError, struct.error):
                continue
            answers, asked = [], []
            for name, qtype in questions:
                if name in self.names:
                    rec = None if (name in self.shared and qtype == TYPE_AAAA) else answer_for(name, qtype, self.ip)
                    if rec:
                        answers.append(rec)
                        asked.append((name, qtype))
            if answers:
                try:
                    if addr[1] == PORT:                 # requête mDNS : réponse multicast
                        s.sendto(build_response(0, answers), (MCAST, PORT))
                    else:                               # résolveur « classique » : réponse directe
                        s.sendto(build_response(qid, answers, asked), addr)
                except OSError:
                    pass
            if time.time() - last > 60:                 # ré-annonce périodique
                last = time.time()
                try:
                    new_ip = local_ip()
                except OSError:
                    new_ip = self.ip
                if new_ip != self.ip:                   # le Pi a changé d'adresse : on rouvre la socket sur la nouvelle
                    self.log(f"[mdns] adresse changée : {self.ip} -> {new_ip}")
                    self.ip = new_ip
                    try:
                        s.close()
                        s = self._socket()
                    except OSError:
                        pass
                try:
                    self.announce(s)
                except OSError:
                    pass
