// bip2split -- turn bipartite graphs into split graphs.
//
// Reads graph6 on stdin; for each graph it adds all edges among the first
// --clique-size vertices, turning that side into a clique while the other side
// stays an independent set. Composed with genbg (and shortg) this enumerates
// split graphs directly instead of filtering them out of all graphs -- see
// scripts/gen_split_n.sh.
#include <cstdlib>
#include <iostream>
#include <string>

#include "small_graph.hpp"

namespace {

int ParseCliqueSize(int argc, char** argv) {
  int clique_size = -1;
  for (int i = 1; i < argc; ++i) {
    std::string arg = argv[i];
    if (arg == "--clique-size" && i + 1 < argc) {
      clique_size = std::atoi(argv[++i]);
      continue;
    }
    if (arg == "-h" || arg == "--help") {
      std::cerr << "Usage: bip2split --clique-size K < bipartite.g6 > split.g6\n";
      std::exit(0);
    }
    std::cerr << "Unknown arg: " << arg << "\n";
    std::exit(2);
  }
  if (clique_size < 0 || clique_size > dern::kMaxN) {
    std::cerr << "--clique-size must be in 0..16\n";
    std::exit(2);
  }
  return clique_size;
}

}  // namespace

int main(int argc, char** argv) {
  const int k = ParseCliqueSize(argc, argv);

  std::ios::sync_with_stdio(false);
  std::cin.tie(nullptr);

  std::string line;
  while (std::getline(std::cin, line)) {
    std::string err;
    auto parsed = dern::ParseGraph6Line(line, &err);
    if (!parsed.has_value()) {
      if (!err.empty()) {
        std::cerr << "Skipping line (parse error): " << err << "\n";
      }
      continue;
    }

    dern::Graph16 g = *parsed;
    if (k > g.n) {
      std::cerr << "Error: clique size " << k << " exceeds n=" << static_cast<int>(g.n) << "\n";
      return 2;
    }

    for (int i = 0; i < k; ++i) {
      for (int j = i + 1; j < k; ++j) dern::AddEdge(&g, i, j);
    }

    std::cout << dern::ToGraph6(g) << "\n";
  }
  return 0;
}

