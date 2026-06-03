// dern -- degree-associated edge reconstruction number for split graphs.
//
// Reads one graph6 string per line on stdin and writes a CSV row
// "n,m,g6,dern,witness" per graph (see --help for flags). For each graph it
// canonicalizes with nauty, builds the degree-associated edge cards, and finds
// the smallest number of cards that force the graph. Candidate parents of a
// card are generated only along non-edges whose endpoint degree-sum matches the
// card's degree label, which sharply constrains the search and keeps dern
// tractable through n = 14.
#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <optional>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include "nauty.h"

#include "small_graph.hpp"

namespace {

struct CanonResult {
  dern::Graph16 canon;
  dern::GraphKey key;
};

CanonResult Canonicalize(const dern::Graph16& g) {
  const int n = static_cast<int>(g.n);
  const int m = SETWORDSNEEDED(n);

  static_assert(dern::kMaxN <= 16, "kMaxN must fit in 2x64-bit GraphKey encoding");
  static bool nauty_checked = false;
  if (!nauty_checked) {
    nauty_check(WORDSIZE, SETWORDSNEEDED(dern::kMaxN), dern::kMaxN, NAUTYVERSIONID);
    nauty_checked = true;
  }

  std::array<graph, dern::kMaxN * SETWORDSNEEDED(dern::kMaxN)> gg{};
  std::array<graph, dern::kMaxN * SETWORDSNEEDED(dern::kMaxN)> canong{};

  EMPTYGRAPH(gg.data(), m, n);
  for (int u = 0; u < n; ++u) {
    for (int v = u + 1; v < n; ++v) {
      if (!dern::HasEdge(g, u, v)) continue;
      ADDONEEDGE(gg.data(), u, v, m);
    }
  }

  std::array<int, dern::kMaxN> lab{};
  std::array<int, dern::kMaxN> ptn{};
  std::array<int, dern::kMaxN> orbits{};
  for (int i = 0; i < n; ++i) {
    lab[static_cast<size_t>(i)] = i;
    ptn[static_cast<size_t>(i)] = 1;
  }
  if (n > 0) ptn[static_cast<size_t>(n - 1)] = 0;

  DEFAULTOPTIONS_GRAPH(options);
  options.getcanon = TRUE;

  statsblk stats;
  densenauty(gg.data(), lab.data(), ptn.data(), orbits.data(), &options, &stats, m, n, canong.data());

  dern::Graph16 canon;
  canon.n = g.n;
  for (int u = 0; u < n; ++u) canon.adj[static_cast<size_t>(u)] = 0;
  for (int u = 0; u < n; ++u) {
    for (int v = u + 1; v < n; ++v) {
      if (!ISELEMENT(GRAPHROW(canong.data(), u, m), v)) continue;
      dern::AddEdge(&canon, u, v);
    }
  }

  return CanonResult{.canon = canon, .key = dern::KeyFromGraph(canon)};
}

struct CardKey {
  dern::GraphKey h_key;
  uint8_t d = 0;

  friend bool operator==(const CardKey& a, const CardKey& b) {
    return a.d == b.d && a.h_key == b.h_key;
  }
};

struct CardKeyHash {
  size_t operator()(const CardKey& c) const noexcept {
    dern::GraphKeyHash gh;
    uint64_t x = static_cast<uint64_t>(gh(c.h_key));
    x ^= static_cast<uint64_t>(c.d) + 0x9e3779b97f4a7c15ULL + (x << 6) + (x >> 2);
    return static_cast<size_t>(x);
  }
};

std::string CardToString(const CardKey& c) {
  const dern::Graph16 h = dern::GraphFromKey(c.h_key);
  return "d=" + std::to_string(static_cast<int>(c.d)) + ";H=" + dern::ToGraph6(h);
}

std::vector<dern::GraphKey> GenerateCandidateGraphs(const CardKey& card) {
  const dern::Graph16 h = dern::GraphFromKey(card.h_key);
  const auto deg = dern::Degrees(h);

  std::vector<dern::GraphKey> out;
  out.reserve(32);

  for (int x = 0; x < h.n; ++x) {
    for (int y = x + 1; y < h.n; ++y) {
      if (dern::HasEdge(h, x, y)) continue;
      const int sum = static_cast<int>(deg[static_cast<size_t>(x)]) + static_cast<int>(deg[static_cast<size_t>(y)]);
      if (sum != static_cast<int>(card.d)) continue;

      dern::Graph16 candidate = h;
      dern::AddEdge(&candidate, x, y);
      out.push_back(Canonicalize(candidate).key);
    }
  }

  std::sort(out.begin(), out.end());
  out.erase(std::unique(out.begin(), out.end()), out.end());
  return out;
}

bool IntersectionIsExactlyG(const std::vector<dern::GraphKey>& a,
                            const std::vector<dern::GraphKey>& b,
                            const dern::GraphKey& g_key) {
  size_t i = 0;
  size_t j = 0;
  bool found_g = false;

  while (i < a.size() && j < b.size()) {
    if (a[i] < b[j]) {
      ++i;
      continue;
    }
    if (b[j] < a[i]) {
      ++j;
      continue;
    }
    // equal
    if (!(a[i] == g_key)) return false;
    found_g = true;
    ++i;
    ++j;
  }
  return found_g;
}

int CardMultiplicityInGraph(const CardKey& card, const dern::GraphKey& g_key) {
  dern::Graph16 g = dern::GraphFromKey(g_key);
  const auto deg = dern::Degrees(g);

  int count = 0;
  for (int u = 0; u < g.n; ++u) {
    for (int v = u + 1; v < g.n; ++v) {
      if (!dern::HasEdge(g, u, v)) continue;
      const int d_uv = static_cast<int>(deg[static_cast<size_t>(u)]) + static_cast<int>(deg[static_cast<size_t>(v)]) - 2;
      if (d_uv != static_cast<int>(card.d)) continue;

      dern::Graph16 h = g;
      dern::RemoveEdge(&h, u, v);
      if (Canonicalize(h).key == card.h_key) ++count;
    }
  }
  return count;
}

struct Args {
  int limit = -1;
  int progress_every = 0;
  bool header = true;
  int min_output_dern = 0;
  bool summary = false;
};

Args ParseArgs(int argc, char** argv) {
  Args a;
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    if ((arg == "-h") || (arg == "--help")) {
      std::cerr << "Usage: dern [--limit N] [--progress-every N] [--no-header]\n"
                   "            [--min-output-dern N] [--summary]\n"
                   "          < graphs.g6 > out.csv\n";
      std::exit(0);
    }
    if (arg == "--limit" && i + 1 < argc) {
      a.limit = std::atoi(argv[++i]);
      continue;
    }
    if (arg == "--progress-every" && i + 1 < argc) {
      a.progress_every = std::atoi(argv[++i]);
      continue;
    }
    if (arg == "--no-header") {
      a.header = false;
      continue;
    }
    if (arg == "--min-output-dern" && i + 1 < argc) {
      a.min_output_dern = std::atoi(argv[++i]);
      continue;
    }
    if (arg == "--summary") {
      a.summary = true;
      continue;
    }
    std::cerr << "Unknown arg: " << arg << "\n";
    std::exit(2);
  }
  return a;
}

struct CardInfo {
  CardKey card;
  int multiplicity_in_g = 0;
  std::vector<dern::GraphKey> candidates;
};

}  // namespace

int main(int argc, char** argv) {
  const Args args = ParseArgs(argc, argv);

  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);

  if (args.header) {
    std::cout << "n,m,g6,dern,witness\n";
  }

  int processed = 0;
  uint64_t count_dern0 = 0;
  uint64_t count_dern1 = 0;
  uint64_t count_dern2 = 0;
  uint64_t count_dern3p = 0;
  std::string line;

  while (std::getline(std::cin, line)) {
    if (args.limit >= 0 && processed >= args.limit) break;

    std::string err;
    auto parsed = dern::ParseGraph6Line(line, &err);
    if (!parsed.has_value()) {
      if (!err.empty()) std::cerr << "Skipping line (parse error): " << err << "\n";
      continue;
    }

    const CanonResult gcanon = Canonicalize(*parsed);
    const dern::Graph16 g = gcanon.canon;
    const dern::GraphKey g_key = gcanon.key;

    const int n = g.n;
    const int m_edges = dern::EdgeCount(g);
    const auto deg = dern::Degrees(g);

    if (m_edges == 0) {
      const int dern_value = 0;
      if (dern_value >= args.min_output_dern) {
        std::cout << n << "," << m_edges << "," << dern::ToGraph6(g) << "," << dern_value << "," << "" << "\n";
      }
      ++count_dern0;
      ++processed;
      continue;
    }

    int dern_value = 3;
    std::string witness;

    // Build the da-ecard types for G, and try to find a DERN=1 witness early.
    std::unordered_map<CardKey, size_t, CardKeyHash> index_by_card;
    index_by_card.reserve(static_cast<size_t>(m_edges * 2));
    std::vector<CardInfo> infos;
    infos.reserve(static_cast<size_t>(m_edges));

    bool found_dern1 = false;

    for (int u = 0; u < n && !found_dern1; ++u) {
      for (int v = u + 1; v < n && !found_dern1; ++v) {
        if (!dern::HasEdge(g, u, v)) continue;
        const int d_uv = static_cast<int>(deg[static_cast<size_t>(u)]) + static_cast<int>(deg[static_cast<size_t>(v)]) - 2;
        if (d_uv < 0 || d_uv > 255) continue;

        dern::Graph16 h = g;
        dern::RemoveEdge(&h, u, v);
        const dern::GraphKey h_key = Canonicalize(h).key;
        const CardKey ck{.h_key = h_key, .d = static_cast<uint8_t>(d_uv)};

        auto [it, inserted] = index_by_card.emplace(ck, infos.size());
        if (!inserted) {
          infos[it->second].multiplicity_in_g += 1;
          continue;
        }

        CardInfo info;
        info.card = ck;
        info.multiplicity_in_g = 1;
        info.candidates = GenerateCandidateGraphs(ck);
        if (info.candidates.size() == 1 && info.candidates[0] == g_key) {
          dern_value = 1;
          witness = CardToString(ck);
          found_dern1 = true;
          break;
        }
        infos.push_back(std::move(info));
      }
    }

    if (dern_value == 1) {
      if (dern_value >= args.min_output_dern) {
        std::cout << n << "," << m_edges << "," << dern::ToGraph6(g) << "," << dern_value << "," << witness << "\n";
      }
      ++count_dern1;
      ++processed;
      if (args.progress_every > 0 && (processed % args.progress_every) == 0) {
        std::cerr << "Processed " << processed << " graphs\n";
      }
      continue;
    }

    if (dern_value != 1) {
      // DERN = 2 with two distinct card types.
      std::vector<size_t> order(infos.size());
      for (size_t i = 0; i < order.size(); ++i) order[i] = i;
      std::sort(order.begin(), order.end(), [&](size_t a, size_t b) {
        return infos[a].candidates.size() < infos[b].candidates.size();
      });

      for (size_t oi = 0; oi < order.size() && dern_value != 2; ++oi) {
        for (size_t oj = oi + 1; oj < order.size() && dern_value != 2; ++oj) {
          const CardInfo& a = infos[order[oi]];
          const CardInfo& b = infos[order[oj]];
          if (IntersectionIsExactlyG(a.candidates, b.candidates, g_key)) {
            dern_value = 2;
            witness = CardToString(a.card) + "|" + CardToString(b.card);
            break;
          }
        }
      }
    }

    if (dern_value != 1 && dern_value != 2) {
      // DERN = 2 with two copies of the same card type (multiplicity distinguishes).
      for (const CardInfo& info : infos) {
        if (info.multiplicity_in_g < 2) continue;

        bool some_other_has_two = false;
        for (const auto& candidate_key : info.candidates) {
          if (candidate_key == g_key) continue;
          if (CardMultiplicityInGraph(info.card, candidate_key) >= 2) {
            some_other_has_two = true;
            break;
          }
        }

        if (!some_other_has_two) {
          dern_value = 2;
          witness = "2x(" + CardToString(info.card) + ")";
          break;
        }
      }
    }

    if (dern_value >= args.min_output_dern) {
      std::cout << n << "," << m_edges << "," << dern::ToGraph6(g) << "," << dern_value << "," << witness << "\n";
    }

    if (dern_value == 0) {
      ++count_dern0;
    } else if (dern_value == 1) {
      ++count_dern1;
    } else if (dern_value == 2) {
      ++count_dern2;
    } else {
      ++count_dern3p;
    }

    ++processed;
    if (args.progress_every > 0 && (processed % args.progress_every) == 0) {
      std::cerr << "Processed " << processed << " graphs\n";
    }
  }

  if (args.summary) {
    std::cerr << "Summary: processed=" << processed << " dern0=" << count_dern0 << " dern1=" << count_dern1
              << " dern2=" << count_dern2 << " dern>=3=" << count_dern3p << "\n";
  }

  return 0;
}
