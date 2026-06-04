# Notes on the ERN and DERN pipelines

This is a design note for the two solvers in this repository: what they do, how
they are structured, and why they are fast enough to be useful. It is about the
*computation*; the mathematical findings belong to the thesis and are not
discussed here.

There are two solvers, and they share a front end:

- **ERN** — the edge reconstruction number — is computed by the Python package
  in [`src/ern/`](../src/ern).
- **DERN** — the degree-associated variant — is computed by the compiled C++
  tool in [`cpp/`](../cpp) (`dern`).

Both consume the same stream of split graphs from
[`scripts/gen_split_n.sh`](../scripts/gen_split_n.sh), and both spend almost all
of their time answering one question: *are these two graphs isomorphic?* nauty
answers it by **canonical labeling** — rewriting a graph into the unique
representative of its isomorphism class so that "isomorphic" becomes "equal
strings." Canonicalization is by far the most expensive thing either solver
does, so nearly every decision below comes down to the same goal: call it less
often, on fewer graphs, and in larger batches.

A useful framing throughout: separate the *exact* reductions (which provably do
not change the answer) from the *tuning knobs* (which only trade time for memory
or parallelism). The last section lists which is which.

## 1. Generate only the graphs you need

The naive way to study split graphs is to enumerate every graph on `n` vertices
with `geng` and discard the ones that aren't split. The fraction that survive
shrinks quickly with `n`, so this wastes almost all of the work before the real
computation even starts.

Instead, [`gen_split_n.sh`](../scripts/gen_split_n.sh) builds split graphs
directly. A split graph is a clique plus an independent set, so for each split
point `k` it asks `genbg` for the bipartite graphs between a `k`-side and an
`(n−k)`-side, then [`bip2split`](../cpp/bip2split.cpp) turns the `k`-side into a
clique by adding its missing edges. Every output is a split graph; nothing is
generated only to be thrown away. The stream is then passed through `shortg`,
which collapses it to one representative per isomorphism class, so the solver
never sees the same unlabeled graph twice.

This is the single biggest lever in the whole project, because it changes the
size of the input before any solving happens.

## 2. Work on the graph6 text, not graph objects

Both the edge-deck and the candidate-generation steps mutate a graph one edge at
a time, many times per graph. Rebuilding and re-encoding a graph object for each
edge would dominate the runtime, so the Python side never does that.

[`decode_graph6_with_edge_bits`](../src/ern/graph6.py) reads a graph6 line once
into per-vertex adjacency bitsets *and* records the exact payload bit position of
every present edge. With that in hand:

- [`edge_deleted_card_counter`](../src/ern/graph6.py) produces the edge-deck by
  flipping one payload bit off, copying the resulting string, and flipping it
  back — a couple of byte operations per card instead of a full re-encode.
- [`single_edge_extensions`](../src/ern/graph6.py) does the mirror image for
  adding an edge.

Recognizing split graphs is likewise cheap: [`is_split_graph`](../src/ern/split.py)
uses the Hammer–Simeone degree-sequence criterion, so it never searches for a
partition — it's a sort and an arithmetic check on the degree sequence.

## 3. Spend the canonicalization budget wisely

Since canonicalization is the bottleneck, the canonicalizers in
[`canonical.py`](../src/ern/canonical.py) are built to amortize it:

- **Batch.** `labelg` is launched once per *batch* of graph6 strings, not once
  per graph, which spreads its process-startup cost across thousands of inputs.
- **Cache.** Repeated cards are common, so canonical forms can be memoized
  (`Graph6BatchCanonicalizer`, with an LRU bound so memory stays flat on long
  runs). `NautyCanonicalizer` additionally hands back small integer ids, which
  the global solver uses to index cards instead of comparing strings.
- **Overlap I/O with work.** Several `labelg` batches can be in flight at once
  via a thread pool — the threads aren't doing the canonicalization, they're
  just keeping multiple subprocesses busy.

The global solver also defers canonicalization: it buffers raw cards and only
flushes a batch to `labelg` once enough have accumulated.

## 4. ERN: shrink the field of suspects

`ern(G)` is the smallest number of edge-cards that pin `G` down: the least `k`
such that some `k` cards from `G`'s deck appear together in *no* other graph's
deck. The exact decision is reduced to counting in
[`reconstruction.py`](../src/ern/reconstruction.py). Write the target's distinct
card types with their multiplicities as `g_counts`, and describe each rival
("blocker") by how many copies of each of those types it can supply. Then `ern`
is the smallest `k` for which some size-`k` choice of cards from the target
cannot be covered by any single blocker. That core is solved in three tiers:

1. **Dominance pruning.** A blocker that has at least as many of *every* relevant
   card type as another is strictly stronger, so only the componentwise-maximal
   blocker vectors are kept (`maximal_blocker_vectors`).
2. **`k = 1` and `k = 2` by bitmask.** Most graphs are decided here:
   `fast_ern_k1_k2` builds, per card type, the bitset of blockers that hold one
   or two copies, and reads off the answer with a few intersections.
3. **`k ≥ 3` by memoized search.** Only when the easy cases fail does
   `exists_distinguishing_subset_of_size` run a DP over
   `(card type, cards left to choose, blockers still compatible)`, where the last
   component is a bitmask that empties exactly when the chosen cards force `G`.

The interesting question is how to get a *small, correct* set of blockers to feed
that core. There are two strategies.

**Global mode** ([`global_solver.py`](../src/ern/global_solver.py)) enumerates
every graph of the order and compares decks. It keeps the comparison honest but
prunes hard: graphs are bucketed by edge count (only equal-edge graphs can share
a deck), and an inverted index from card type to the graphs containing it means a
target is only ever compared against graphs that actually share one of its cards.
This is the simplest mode to trust and is used as the small-`n` cross-check, but
it pays for generating the whole universe, so it does not scale.

**Local mode** ([`local_solver.py`](../src/ern/local_solver.py)) is the scalable
path, and it rests on one observation. If some graph `H` can be confused with `G`,
then `H` shares an edge-card `C` with `G`; but `C = H − e` means `H = C + e`, so
**every possible blocker is a one-edge extension of one of `G`'s own cards.** So
the solver never looks at the universe at all: it takes `G`'s cards, forms their
one-edge extensions, canonicalizes and de-duplicates them, and that small set is
the only place a blocker can hide. On top of that it:

- caches each card's parent set, since the same card type recurs across many
  targets;
- settles `ern = 1` and `ern = 2` early from parent-set intersections, before
  building any blocker decks; and
- only builds and projects blocker decks for the candidates that survive, keeping
  the vectors fed to the core as small as possible.

## 5. DERN: let the degree label do the work

The degree-associated deck attaches one extra fact to each card: the degree sum
of the deleted edge's endpoints. That single number is what makes DERN both a
different problem and a much easier one to compute, so the C++ solver is built
around it.

The representation is deliberately small ([`small_graph.hpp`](../cpp/small_graph.hpp)):
`Graph16` stores up to sixteen vertices as one 16-bit adjacency row per vertex,
and `GraphKey` packs the upper triangle into two 64-bit words. Edge tests are bit
tests, degrees are popcounts, and hashing or comparing a graph is a couple of
integer operations with no allocation. graph6 parsing and encoding are written
directly against this layout.

The payoff is in candidate generation ([`dern.cpp`](../cpp/dern.cpp)). A card is
the pair *(canonical deleted graph, endpoint degree sum)*. To reconstruct the
parents of a card `H`, the plain ERN solver would have to try adding back every
non-edge; the degree label removes almost all of them, because the endpoints of
the restored edge must have a specific degree sum in `H`. `GenerateCandidateGraphs`
only adds a non-edge `xy` when `deg_H(x) + deg_H(y)` matches the card's label,
canonicalizes the result, and de-duplicates. The candidate set that comes out is
small and exact.

From there the solver mirrors the ERN tiers but on these tighter sets:

- it builds each distinct card type once and counts multiplicities;
- it returns `dern = 1` as soon as a card's only possible parent is `G` itself;
- it tests pairs of cards cheapest-first and intersects their sorted candidate
  lists with a two-pointer walk (`IntersectionIsExactlyG`), never materializing a
  set; and
- it handles the case where two copies of one repeated card already suffice.

Input is processed as a stream, and the per-graph containers are reserved up
front, so memory stays flat regardless of how many graphs are piped in.

## 6. Parallelism

Both solvers parallelize across processes, because the heavy work (nauty, or the
compiled solver) sidesteps Python's GIL entirely.

- **ERN** runs local-mode targets across a `ProcessPoolExecutor`. Each worker
  keeps its own canonicalization and parent-set caches and is fed bounded chunks
  of targets, so scheduling overhead, load balance, and memory stay in check.
- **DERN** uses [`dern_parallel.py`](../scripts/dern_parallel.py) as a pure
  dispatcher: it fans the graph6 stream round-robin across several `dern`
  processes, lets each write its own output file to avoid contention, and merges
  the files and the per-worker summary counters at the end.

## 7. Exact reductions vs. tuning knobs

The following change the *amount* of work but never the answer:

- direct split-graph generation and `shortg` deduplication;
- canonical labeling as the basis for every isomorphism test;
- restricting ERN blockers to one-edge extensions of the target's cards;
- restricting DERN candidates to non-edges with the matching degree sum;
- edge-count bucketing, the card-type inverted index, and dominance pruning;
- the `k = 1, 2` bitmask tests and the `k ≥ 3` DP.

These only trade time against memory or cores, and can be set freely:

- worker count (`--jobs`), chunk and batch sizes, blocker chunk size;
- whether the raw-card cache is on and its size cap;
- `shortg` on/off (off is faster but no longer one graph per isomorphism class);
- how often summaries and progress are printed.

## Where to look in the code

| Concern | Code |
| --- | --- |
| graph6 decode, edge cards, extensions | [`src/ern/graph6.py`](../src/ern/graph6.py) |
| split recognition + target generation | [`src/ern/split.py`](../src/ern/split.py) |
| batched `labelg` canonicalization | [`src/ern/canonical.py`](../src/ern/canonical.py) |
| exact ERN decision core | [`src/ern/reconstruction.py`](../src/ern/reconstruction.py) |
| global / local ERN solvers | [`src/ern/global_solver.py`](../src/ern/global_solver.py), [`src/ern/local_solver.py`](../src/ern/local_solver.py) |
| compact graph type + graph6 I/O | [`cpp/small_graph.hpp`](../cpp/small_graph.hpp) |
| DERN solver | [`cpp/dern.cpp`](../cpp/dern.cpp) |
| split generation + parallel drivers | [`scripts/`](../scripts) |
