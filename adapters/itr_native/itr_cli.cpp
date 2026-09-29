// itr_cli: batch front end for the upstream ITR (Iterative Reconstruction) algorithm.
//
// Replaces upstream DNA.cpp's main() and links every other upstream object unchanged.
// The reconstruction call and its parameters are exactly upstream's
// (TestFromFileCaseRange in DNA.cpp). What differs is documented in
// adapters/itr_native/PATCH.md:
//   1. Input carries no ground truth. ITR reads the original strand only for its length
//      (docs/upstream_notes.md), so a placeholder of the expected length is passed.
//   2. The mt19937 generator is reseeded for every cluster from
//      FNV-1a-64(cluster_id) XOR seed, instead of once per run from the clock.
//   3. Nothing is computed against the original after reconstruction.
//
// Usage: itr_cli --seed <uint64> [input_file]      (reads stdin when no file is given)
//
// Input, one block per cluster:
//   >cluster_id expected_length n_reads
//   read_1
//   ...
//   read_n
// Reads must be non-empty and contain only A/C/G/T. n_reads may be 0.
//
// Output (stdout), TSV with a header, one row per cluster, flushed after each row so a
// killed process still leaves its finished rows:
//   cluster_id  status  reads_in  reads_used  runtime_ms  sequence
// status: ok | single_read (the only read is returned, as upstream) |
//         empty_cluster (no reads, sequence empty) | error (exception, message on stderr)
// Malformed input stops the run with exit code 2 after the rows already written.

#include <chrono>
#include <cerrno>
#include <cstdint>
#include <cstdlib>
#include <exception>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <random>
#include <sstream>
#include <string>
#include <vector>

#include "Cluster2.hpp"

namespace {

// Upstream values from DNA.cpp main(); do not change (they define "ITR").
const int kMaxCopies = 25;      // keep the first 25 reads in input order
const int kDelPatternLen = 3;
const int kSubPriority = 0;
const int kDelPriority = 0;
const int kInsPriority = 0;
const int kMaxReps = 2;

uint64_t Fnv1a64(const std::string& text) {
    uint64_t hash = 14695981039346656037ULL;
    for (unsigned char c : text) {
        hash ^= c;
        hash *= 1099511628211ULL;
    }
    return hash;
}

// The per-cluster generator. seed_seq takes both 32-bit halves, so all 64 bits count.
std::mt19937 ClusterGenerator(const std::string& cluster_id, uint64_t seed) {
    uint64_t mixed = Fnv1a64(cluster_id) ^ seed;
    std::seed_seq seq{static_cast<uint32_t>(mixed), static_cast<uint32_t>(mixed >> 32)};
    return std::mt19937(seq);
}

[[noreturn]] void Malformed(long line_no, const std::string& message) {
    std::cout.flush();
    std::cerr << "itr_cli: input line " << line_no << ": " << message << std::endl;
    std::exit(2);
}

bool IsDna(const std::string& read) {
    for (char c : read) {
        if (c != 'A' && c != 'C' && c != 'G' && c != 'T') {
            return false;
        }
    }
    return true;
}

bool ParseSeed(const char* text, uint64_t& seed) {
    char* end = nullptr;
    errno = 0;
    unsigned long long value = std::strtoull(text, &end, 10);
    if (errno != 0 || end == text || *end != '\0' || text[0] == '-') {
        return false;
    }
    seed = value;
    return true;
}

void Usage() {
    std::cerr << "usage: itr_cli --seed <uint64> [input_file]" << std::endl;
    std::exit(2);
}

}  // namespace

int main(int argc, char* argv[]) {
    uint64_t seed = 0;
    bool have_seed = false;
    std::string input_path;
    for (int i = 1; i < argc; i++) {
        std::string arg = argv[i];
        if (arg == "--seed" && i + 1 < argc) {
            if (!ParseSeed(argv[++i], seed)) {
                std::cerr << "itr_cli: --seed must be an unsigned 64-bit integer" << std::endl;
                return 2;
            }
            have_seed = true;
        } else if (!arg.empty() && arg[0] != '-' && input_path.empty()) {
            input_path = arg;
        } else {
            Usage();
        }
    }
    if (!have_seed) {
        Usage();
    }

    std::ifstream file;
    if (!input_path.empty()) {
        file.open(input_path);
        if (!file.is_open()) {
            std::cerr << "itr_cli: cannot open " << input_path << std::endl;
            return 2;
        }
    }
    std::istream& input = input_path.empty() ? std::cin : file;

    std::cout << "cluster_id\tstatus\treads_in\treads_used\truntime_ms\tsequence\n" << std::flush;

    std::string line;
    long line_no = 0;
    int failed = 0;
    while (std::getline(input, line)) {
        line_no++;
        if (line.empty()) {
            continue;  // blank lines between blocks are allowed
        }
        if (line[0] != '>') {
            Malformed(line_no, "expected a '>cluster_id expected_length n_reads' header");
        }
        std::istringstream header(line.substr(1));
        std::string cluster_id, extra;
        long expected_length = 0, n_reads = -1;
        if (!(header >> cluster_id >> expected_length >> n_reads) || (header >> extra)) {
            Malformed(line_no, "header must have exactly three fields");
        }
        if (expected_length <= 0 || n_reads < 0) {
            Malformed(line_no, "expected_length must be > 0 and n_reads >= 0");
        }

        std::vector<std::string> copies;  // the first kMaxCopies reads, as upstream
        for (long r = 0; r < n_reads; r++) {
            if (!std::getline(input, line)) {
                Malformed(line_no, "cluster " + cluster_id + " ends before its " +
                                       std::to_string(n_reads) + " reads");
            }
            line_no++;
            if (line.empty() || !IsDna(line)) {
                Malformed(line_no, "reads must be non-empty and contain only A/C/G/T");
            }
            if ((long)copies.size() < kMaxCopies) {
                copies.push_back(line);
            }
        }

        auto start = std::chrono::steady_clock::now();
        std::string status, guess;
        if (copies.empty()) {
            status = "empty_cluster";
        } else if (copies.size() == 1) {
            status = "single_read";
            guess = copies[0];
        } else {
            try {
                std::mt19937 generator = ClusterGenerator(cluster_id, seed);
                std::string placeholder(expected_length, 'A');
                Cluster2 cluster(placeholder, copies);
                int unused_edit_dist = 0;  // upstream's out-parameter; TestBest never sets it
                guess = cluster.TestBest(kDelPatternLen, unused_edit_dist, kSubPriority,
                                         kDelPriority, kInsPriority, generator, kMaxReps);
                status = "ok";
            } catch (const std::exception& e) {
                status = "error";
                guess.clear();
                failed++;
                std::cerr << "itr_cli: " << cluster_id << ": " << e.what() << std::endl;
            }
        }
        double runtime_ms = std::chrono::duration<double, std::milli>(
                                std::chrono::steady_clock::now() - start).count();

        std::cout << cluster_id << '\t' << status << '\t' << n_reads << '\t' << copies.size()
                  << '\t' << std::fixed << std::setprecision(3) << runtime_ms << '\t' << guess
                  << '\n' << std::flush;
    }
    return failed > 0 ? 1 : 0;
}
