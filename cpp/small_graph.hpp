// small_graph.hpp -- fixed-size graph representation and graph6 I/O.
//
// Graph16 stores up to 16 vertices as one uint16 adjacency bitset per vertex;
// GraphKey packs the upper triangle into two 64-bit words for cheap hashing and
// equality. Also provides direct graph6 parsing/encoding tuned to this layout.
#pragma once

#include <array>
#include <cstdint>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

namespace dern {

constexpr int kMaxN = 16;  // supports n<=15 target (and n=16 optionally)

struct Graph16 {
  uint8_t n = 0;
  std::array<uint16_t, kMaxN> adj{};
};

struct GraphKey {
  uint8_t n = 0;
  uint64_t lo = 0;
  uint64_t hi = 0;

  friend bool operator==(const GraphKey& a, const GraphKey& b) {
    return a.n == b.n && a.lo == b.lo && a.hi == b.hi;
  }
  friend bool operator<(const GraphKey& a, const GraphKey& b) {
    if (a.n != b.n) return a.n < b.n;
    if (a.hi != b.hi) return a.hi < b.hi;
    return a.lo < b.lo;
  }
};

struct GraphKeyHash {
  size_t operator()(const GraphKey& k) const noexcept {
    // 64-bit mix
    uint64_t x = (static_cast<uint64_t>(k.n) << 1) ^ k.lo;
    x ^= k.hi + 0x9e3779b97f4a7c15ULL + (x << 6) + (x >> 2);
    x ^= x >> 33;
    x *= 0xff51afd7ed558ccdULL;
    x ^= x >> 33;
    x *= 0xc4ceb9fe1a85ec53ULL;
    x ^= x >> 33;
    return static_cast<size_t>(x);
  }
};

inline bool HasEdge(const Graph16& g, int u, int v) {
  return (g.adj.at(static_cast<size_t>(u)) >> v) & 1U;
}

inline void AddEdge(Graph16* g, int u, int v) {
  if (u == v) return;
  g->adj.at(static_cast<size_t>(u)) |= static_cast<uint16_t>(1U << v);
  g->adj.at(static_cast<size_t>(v)) |= static_cast<uint16_t>(1U << u);
}

inline void RemoveEdge(Graph16* g, int u, int v) {
  if (u == v) return;
  g->adj.at(static_cast<size_t>(u)) &= static_cast<uint16_t>(~(1U << v));
  g->adj.at(static_cast<size_t>(v)) &= static_cast<uint16_t>(~(1U << u));
}

inline int Popcount16(uint16_t x) {
  return __builtin_popcount(static_cast<unsigned int>(x));
}

inline int EdgeCount(const Graph16& g) {
  int sum = 0;
  for (int v = 0; v < g.n; ++v) sum += Popcount16(g.adj[static_cast<size_t>(v)]);
  return sum / 2;
}

inline std::array<uint8_t, kMaxN> Degrees(const Graph16& g) {
  std::array<uint8_t, kMaxN> deg{};
  for (int v = 0; v < g.n; ++v) deg[static_cast<size_t>(v)] = static_cast<uint8_t>(Popcount16(g.adj[static_cast<size_t>(v)]));
  return deg;
}

inline int UpperTriangleIndex(int i, int j) {
  // i<j, order: (0,1), (0,2),(1,2), (0,3),(1,3),(2,3), ...
  return (j * (j - 1)) / 2 + i;
}

inline GraphKey KeyFromGraph(const Graph16& g) {
  GraphKey k;
  k.n = g.n;
  int idx = 0;
  for (int j = 1; j < g.n; ++j) {
    for (int i = 0; i < j; ++i, ++idx) {
      if (!HasEdge(g, i, j)) continue;
      if (idx < 64) {
        k.lo |= (1ULL << idx);
      } else {
        k.hi |= (1ULL << (idx - 64));
      }
    }
  }
  return k;
}

inline bool KeyHasEdgeBit(const GraphKey& k, int idx) {
  if (idx < 64) return (k.lo >> idx) & 1ULL;
  return (k.hi >> (idx - 64)) & 1ULL;
}

inline Graph16 GraphFromKey(const GraphKey& k) {
  Graph16 g;
  g.n = k.n;
  int idx = 0;
  for (int j = 1; j < g.n; ++j) {
    for (int i = 0; i < j; ++i, ++idx) {
      if (KeyHasEdgeBit(k, idx)) AddEdge(&g, i, j);
    }
  }
  return g;
}

inline std::optional<Graph16> ParseGraph6Line(std::string_view line, std::string* err) {
  while (!line.empty() && (line.back() == '\n' || line.back() == '\r')) line.remove_suffix(1);
  if (line.empty()) return std::nullopt;
  if (line.rfind(">>graph6<<", 0) == 0) return std::nullopt;
  if (line[0] == '>') return std::nullopt;  // other headers
  if (line[0] == ':') {
    if (err) *err = "sparse6 (:) not supported; convert to graph6";
    return std::nullopt;
  }
  if (line[0] == '~') {
    if (err) *err = "graph6 n>62 not supported";
    return std::nullopt;
  }

  int n = static_cast<unsigned char>(line[0]) - 63;
  if (n < 0 || n > kMaxN) {
    if (err) *err = "n out of range (supports n<=16)";
    return std::nullopt;
  }
  Graph16 g;
  g.n = static_cast<uint8_t>(n);

  const int need_bits = (n * (n - 1)) / 2;
  int bit_pos = 0;
  int char_pos = 1;
  int val = 0;
  int bits_left = 0;

  auto next_bit = [&]() -> int {
    if (bits_left == 0) {
      if (char_pos >= static_cast<int>(line.size())) return 0;
      val = static_cast<unsigned char>(line[static_cast<size_t>(char_pos++)]) - 63;
      bits_left = 6;
    }
    --bits_left;
    return (val >> bits_left) & 1;
  };

  for (int j = 1; j < n; ++j) {
    for (int i = 0; i < j; ++i) {
      if (bit_pos++ >= need_bits) break;
      if (next_bit()) AddEdge(&g, i, j);
    }
  }

  return g;
}

inline std::string ToGraph6(const Graph16& g) {
  const int n = g.n;
  std::string out;
  out.reserve(static_cast<size_t>(1 + ((n * (n - 1) / 2 + 5) / 6)));
  out.push_back(static_cast<char>(n + 63));

  int acc = 0;
  int bits = 0;
  for (int j = 1; j < n; ++j) {
    for (int i = 0; i < j; ++i) {
      const int bit = HasEdge(g, i, j) ? 1 : 0;
      acc = (acc << 1) | bit;
      if (++bits == 6) {
        out.push_back(static_cast<char>(acc + 63));
        acc = 0;
        bits = 0;
      }
    }
  }
  if (bits != 0) {
    acc <<= (6 - bits);
    out.push_back(static_cast<char>(acc + 63));
  }
  return out;
}

}  // namespace dern

