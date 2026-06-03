# Build the C++ tools for the split-graph DERN pipeline.
#
#   dern       - reads graph6 on stdin, writes n,m,g6,dern,witness on stdout
#   bip2split  - turns a bipartite graph6 stream into split graphs
#
# `dern` links against nauty (densenauty + graph6 helpers); `bip2split` does
# not. nauty is located automatically for Homebrew and common Linux layouts;
# override with `make NAUTY_CFLAGS=... NAUTY_LIBS=...` if needed.

CXX      ?= c++
CXXSTD   ?= -std=c++20
CXXFLAGS ?= -O2 -Wall -Wextra $(CXXSTD)

# --- locate nauty (only `dern` needs it) ----------------------------------
NAUTY_PREFIX := $(shell brew --prefix nauty 2>/dev/null)
ifneq ($(NAUTY_PREFIX),)
  NAUTY_CFLAGS ?= -I$(NAUTY_PREFIX)/include/nauty
  NAUTY_LIBS   ?= -L$(NAUTY_PREFIX)/lib -lnauty
else ifneq ($(wildcard /usr/include/nauty/nauty.h),)
  NAUTY_CFLAGS ?= -I/usr/include/nauty
  NAUTY_LIBS   ?= -lnauty
else ifneq ($(wildcard /usr/local/include/nauty/nauty.h),)
  NAUTY_CFLAGS ?= -I/usr/local/include/nauty
  NAUTY_LIBS   ?= -L/usr/local/lib -lnauty
else
  NAUTY_CFLAGS ?=
  NAUTY_LIBS   ?= -lnauty
endif

BIN := bin

.PHONY: all clean
all: $(BIN)/dern $(BIN)/bip2split

$(BIN):
	mkdir -p $(BIN)

$(BIN)/dern: dern.cpp small_graph.hpp | $(BIN)
	$(CXX) $(CXXFLAGS) $(NAUTY_CFLAGS) dern.cpp -o $@ $(NAUTY_LIBS)

$(BIN)/bip2split: bip2split.cpp small_graph.hpp | $(BIN)
	$(CXX) $(CXXFLAGS) bip2split.cpp -o $@

clean:
	rm -rf $(BIN)
