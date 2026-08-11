# -*- coding: utf-8 -*-
"""BM25 text similarity matching between EoICD descriptions and HLR content."""

from __future__ import annotations

import math
import re
from collections import Counter

from app.v4.config import BM25_K1, BM25_B
from app.v4.models import MatchCandidate


def _tokenize(text: str) -> list[str]:
    """Tokenize mixed Chinese/English text into n-gram-like tokens.

    ASCII words: keep as-is (len >= 2).
    Chinese chars: use character bigrams.
    """
    tokens: list[str] = []
    # Extract ASCII word tokens
    for word in re.findall(r"[a-zA-Z0-9]{2,}", text):
        tokens.append(word.lower())
    # Extract Chinese character sequences and build bigrams
    chinese_chars = re.findall(r"[一-鿿]+", text)
    for seq in chinese_chars:
        if len(seq) == 1:
            tokens.append(seq)
        else:
            for i in range(len(seq) - 1):
                tokens.append(seq[i : i + 2])
    return tokens


class TextMatcher:
    """BM25-based text similarity matcher."""

    def __init__(self, k1: float = BM25_K1, b: float = BM25_B):
        self.k1 = k1
        self.b = b
        self._docs: list[list[str]] = []
        self._hlr_ids: list[str] = []
        self._hlr_contents: list[str] = []
        self._doc_len: list[int] = []
        self._avgdl: float = 0.0
        self._df: dict[str, int] = {}  # document frequency
        self._idf: dict[str, float] = {}
        self._N: int = 0

    def fit(self, hlr_labels: dict[str, str]) -> None:
        """Build BM25 index from enriched HLR text.

        Args:
            hlr_labels: dict mapping hlr_id -> enriched_text.
        """
        self._docs = []
        self._hlr_ids = []
        self._hlr_contents = []
        for hlr_id, enriched_text in hlr_labels.items():
            tokens = _tokenize(enriched_text)
            if tokens:
                self._docs.append(tokens)
                self._hlr_ids.append(hlr_id)
                self._hlr_contents.append(enriched_text)
        self._N = len(self._docs)
        if self._N == 0:
            return
        self._doc_len = [len(d) for d in self._docs]
        self._avgdl = sum(self._doc_len) / self._N
        # Compute document frequencies
        self._df = {}
        for doc in self._docs:
            for term in set(doc):
                self._df[term] = self._df.get(term, 0) + 1
        # Compute IDF
        self._idf = {}
        for term, df in self._df.items():
            self._idf[term] = math.log(
                (self._N - df + 0.5) / (df + 0.5) + 1.0
            )

    def score_all(self, eq: "EnrichedQuery") -> list[MatchCandidate]:
        """Score this EnrichedQuery against all indexed HLR docs."""
        return self.score_text(eq.enriched_text)

    def score_text(self, text: str) -> list[MatchCandidate]:
        """Score raw text against all indexed HLR docs.

        Used for profile-level BM25 where descriptions from multiple entries
        are concatenated into a single query.
        """
        if self._N == 0:
            return []
        query_tokens = _tokenize(text)
        if not query_tokens:
            return []
        query_tf = Counter(query_tokens)

        results: list[MatchCandidate] = []
        for idx in range(self._N):
            score = self._score_one(query_tf, idx)
            if score > 0:
                results.append(
                    MatchCandidate(
                        hlr_id=self._hlr_ids[idx],
                        hlr_content=self._hlr_contents[idx],
                        score=round(score, 4),
                        match_source="bm25",
                        matched_fields=["bm25"],
                    )
                )
        # Normalize to 0-20 range
        if results:
            max_score = max(r.score for r in results)
            if max_score > 0:
                for r in results:
                    r.score = round(r.score / max_score * 20, 2)
        results.sort(key=lambda x: x.score, reverse=True)
        return results

    def _score_one(self, query_tf: Counter, doc_idx: int) -> float:
        """BM25 score for a single document."""
        doc = self._docs[doc_idx]
        doc_len = self._doc_len[doc_idx]
        doc_tf = Counter(doc)
        score = 0.0
        for term, qf in query_tf.items():
            if term not in self._idf:
                continue
            tf = doc_tf.get(term, 0)
            if tf == 0:
                continue
            idf = self._idf[term]
            numerator = tf * (self.k1 + 1)
            denominator = tf + self.k1 * (
                1 - self.b + self.b * doc_len / self._avgdl
            )
            score += idf * (numerator / denominator) * qf
        return score
