"""CLIP's byte-pair tokenizer (a faithful re-implementation of OpenAI's MIT-licensed SimpleTokenizer)."""

from __future__ import annotations

import gzip
import html
from functools import lru_cache
from pathlib import Path

import regex as re

SOT, EOT = "<|startoftext|>", "<|endoftext|>"


@lru_cache()
def bytes_to_unicode() -> dict[int, str]:
    bs = list(range(ord("!"), ord("~") + 1)) + list(range(ord("¡"), ord("¬") + 1)) + list(range(ord("®"), ord("ÿ") + 1))
    cs = bs[:]
    n = 0
    for b in range(256):
        if b not in bs:
            bs.append(b)
            cs.append(256 + n)
            n += 1
    return dict(zip(bs, [chr(c) for c in cs]))


def _pairs(word: tuple) -> set:
    return {(a, b) for a, b in zip(word, word[1:])}


def _clean(text: str) -> str:
    text = html.unescape(html.unescape(text)).strip()
    return re.sub(r"\s+", " ", text).strip()


class Tokenizer:
    def __init__(self, bpe_path: str | Path):
        self.byte_encoder = bytes_to_unicode()
        merges = gzip.open(bpe_path).read().decode("utf-8").split("\n")[1:49152 - 256 - 2 + 1]
        merges = [tuple(m.split()) for m in merges]
        vocab = list(self.byte_encoder.values())
        vocab = vocab + [v + "</w>" for v in vocab] + ["".join(m) for m in merges] + [SOT, EOT]
        self.encoder = {t: i for i, t in enumerate(vocab)}
        self.bpe_ranks = {m: i for i, m in enumerate(merges)}
        self.cache = {SOT: SOT, EOT: EOT}
        self.pat = re.compile(r"""<\|startoftext\|>|<\|endoftext\|>|'s|'t|'re|'ve|'m|'ll|'d|[\p{L}]+|[\p{N}]|"""
                              r"""[^\s\p{L}\p{N}]+""", re.IGNORECASE)

    def bpe(self, token: str) -> str:
        if token in self.cache:
            return self.cache[token]
        word = tuple(token[:-1]) + (token[-1] + "</w>",)
        pairs = _pairs(word)
        if not pairs:
            return token + "</w>"
        while True:
            bigram = min(pairs, key=lambda p: self.bpe_ranks.get(p, float("inf")))
            if bigram not in self.bpe_ranks:
                break
            first, second = bigram
            new, i = [], 0
            while i < len(word):
                try:
                    j = word.index(first, i)
                except ValueError:
                    new.extend(word[i:])
                    break
                new.extend(word[i:j])
                i = j
                if word[i] == first and i < len(word) - 1 and word[i + 1] == second:
                    new.append(first + second)
                    i += 2
                else:
                    new.append(word[i])
                    i += 1
            word = tuple(new)
            if len(word) == 1:
                break
            pairs = _pairs(word)
        out = " ".join(word)
        self.cache[token] = out
        return out

    def encode(self, text: str) -> list[int]:
        ids = []
        for token in re.findall(self.pat, _clean(text).lower()):
            token = "".join(self.byte_encoder[b] for b in token.encode("utf-8"))
            ids.extend(self.encoder[t] for t in self.bpe(token).split(" "))
        return ids

    def encode_batch(self, texts: list[str], ctx: int = 77) -> list[list[int]]:
        sot, eot = self.encoder[SOT], self.encoder[EOT]
        rows = []
        for t in texts:
            ids = [sot] + self.encode(t)[:ctx - 2] + [eot]
            rows.append(ids + [0] * (ctx - len(ids)))
        return rows
