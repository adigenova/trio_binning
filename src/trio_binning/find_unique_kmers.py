#!/usr/bin/env python3
"""
find-unique-kmers -- Given multiple short-read libraries
find k-mers that are unique to each.

Author: Edward S. Rice (erice11@unl.edu)
"""

import argparse
import os
import random
import sys
from subprocess import check_call


class HistogramError(Exception):
    def __init__(self, histo_cmd):
        self.message = (
            "Could not find min and max counts in histogram. "
            + "Try running the following command yourself and manually "
            + "choosing cutoffs: {}".format(histo_cmd)
        )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Given multiple short-read "
        "libraries, find k-mers that are unique to each library."
    )
    parser.add_argument("-k", "--kmer-size",   type=int, required=True)
    parser.add_argument("-l", "--count_lower", type=int, default=0,required=False)
    parser.add_argument("-u", "--count_upper", type=int, default=0,required=False)
    parser.add_argument(
        "--path-to-kmc",
        default="kmc",
        help="path to the kmc binary, in case it's not in PATH",
    )
    parser.add_argument(
        "-p",
        "--threads",
        type=int,
        default=1,
        help="number of threads to use in kmc [1]",
    )
    parser.add_argument(
        "-o", "--outpath", default=".", help="Prefix to write " "output haplotypes to"
    )
    parser.add_argument(
        "-s",
        "--scratch-dir",
        default=".",
        help="Directory " "for large temporary files",
    )
    parser.add_argument(
        "read_files",
        nargs=2,
        help="one comma-separated "
        "list of file paths for both libraries being compared. Files can "
        "be in fasta or fastq format, and uncompressed or gzipped.",
    )
    return parser.parse_args()


def run_kmc(infile_paths, outfile_path, k, threads=1, kmc_path="kmc", scratch_dir="."):
    """
    Given a list of input fastq files, run kmc on them.

    Arguments:
    - infile_paths: list of paths to input fasta/q(.gz) file
    - outfile_path: place to put kmc database file
    - k: k-mer size
    - threads: # threads to give kmc
    - kmc_path: path to kmc binary
    - scratch_dir: path for large temporary files
    """

    # write a list of input file paths to a temporary file
    infile_rand_int = str(random.randint(0, 9999999))
    paths_list_file_path = "{}/{}".format(scratch_dir, infile_rand_int)
    with open(paths_list_file_path, "w") as f:
        for path in infile_paths:
            print(path, file=f)

    # run kmc
    kmc_cmd = [
        kmc_path,
        "-k{}".format(k),
        "-t{}".format(threads),
        "@" + paths_list_file_path,
        outfile_path,
        scratch_dir,
    ]

    try:
        check_call(kmc_cmd)
    except FileNotFoundError:
        print(
            "Cannot execute kmc. Make sure kmc is installed and either in\n"
            "your PATH or specified by the --path-to-kmc option.",
            file=sys.stderr,
        )
        sys.exit(1)
    finally:
        # clean up temp file
        os.remove(paths_list_file_path)


def analyze_histogram(kmc_db, kmc_path="kmc", scratch_path="."):
    """
    Given a jellyfish database, run jellyfish histo to
    compute a histogram of k-mer counts, and then use this
    histogram to choose minimum and maximum k-mer counts
    for finding unique k-mers.

    Arguments:
    - jellyfish_db: path to jellyfish database
    - num_threads: number of threads to give jellyfish

    Returns: (min_coverage, max_coverage), the min and max
             k-mer counts for finding unique k-mers
    """

    # run the kmc histogram program
    temp_histogram_path = "{}/{}".format(scratch_path, random.randint(1, 9999999))
    histo_cmd = [
        kmc_path + "_tools",
        "transform",
        kmc_db,
        "histogram",
        temp_histogram_path,
    ]
    check_call(histo_cmd)

    # go through the histogram file looking for the first local minimum
    min_coverage, max_coverage = False, False
    last_count = -1
    for line in open(temp_histogram_path):
        coverage, count = map(int, line.strip().split())
        if coverage != 2:  # don't do anything except record the first entry

            # if we haven't yet found the minimum coverage, we're looking for
            # a local minimum, i.e., a place where count starts increasing
            if not min_coverage:
                if count > last_count:
                    min_coverage = coverage - 1
                    min_coverage_count = last_count

            # if we have found the minimum coverage already, we're looking for
            # the place where the count dips below the count at min_coverage
            elif not max_coverage:
                if count < min_coverage_count:
                    max_coverage = coverage
                    break

        last_count = count

    if not min_coverage or not max_coverage:
        raise HistogramError(histo_cmd)

    if max_coverage - min_coverage < 5:
        print(
            "WARNING: min and max coverage not very far apart. This may be "
            "a result of coverage being too low. Try taking a look at "
            'the histogram in "{}" yourself.'.format(temp_histogram_path),
            file=sys.stderr,
        )
    else:
        os.remove(temp_histogram_path)

    return min_coverage, max_coverage


def run_kmc_subtract(kmc_db_a, kmc_db_b, kmc_cmd="kmc"):
    """
    Given two kmc databases along with min and max counts
    for both, make a new database containing only k-mers
    that appear in the first database but not the second,
    within the min and max bounds given.

    Arguments:
    - kmc_db_a: path to the db to be subtracted _from_
    - kmc_db_b: path to the db to subtract
    - kmc_cmd: path to kmc binary (kmc_tools should be in
      the same place).

    Returns: path to the output database
    """

    out_path = kmc_db_a + "_only"
    subtract_cmd = [
        kmc_cmd + "_tools",
        "simple",
        kmc_db_a,
        kmc_db_b,
        "kmers_subtract",
        out_path,
    ]
    check_call(subtract_cmd)
    return out_path


def run_kmc_dump(kmc_db, out_path, min_count, max_count, kmc_cmd="kmc"):
    """
    Given a kmc database, dump it into a text file.

    Arguments:
    - kmc_db: path to kmc database
    - out_path: path where we'll put result
    - min_count: minimum count to output k-mer
    - max_count: maximum count to output k-mer
    - kmc_cmd: path to kmc binary

    Returns: number of kmers dumped
    """

    dump_cmd = [
        kmc_cmd + "_dump",
        "-ci" + str(min_count),
        "-cx" + str(max_count),
        kmc_db,
        out_path,
    ]
    check_call(dump_cmd)

    num_lines = 0
    with open(out_path) as f:
        for line in f:
            num_lines += 1
    return num_lines


def main():
    args = parse_args()

    # Count k-mers in all haplotypes
    kmc_databases = []  # list of databases (db_path, min_count, max_count)
    for hap_ID, haplotype_files_string in zip(["A", "B"], args.read_files):
        print(
            "\033[92mCounting k-mers in haplotype {}...\033[0m".format(hap_ID),
            file=sys.stderr,
        )
        haplotype_files = haplotype_files_string.split(",")
        outfile_path = "{}/haplotype{}".format(args.outpath, hap_ID)
        run_kmc(
            haplotype_files,
            outfile_path,
            args.kmer_size,
            args.threads,
            args.path_to_kmc,
            args.scratch_dir,
        )

        # get the histogram for this haplotype and analyze it
        if(args.l == args.u == 0):
         print("\033[92mComputing and analyzing histogram...\033[0m", file=sys.stderr)
         min_count, max_count = analyze_histogram(
             outfile_path, args.path_to_kmc, args.scratch_dir
         )
        else:
         min_count = args.l
         max_count = args.u
        
        print(
            "\033[92mUsing counts in range [{},{}].\033[0m".format(
                min_count, max_count
            ),
            file=sys.stderr,
        )

        kmc_databases.append((outfile_path, min_count, max_count))

    # unpack the database paths and min/max counts
    (kmc_db_a, min_count_a, max_count_a) = kmc_databases[0]
    (kmc_db_b, min_count_b, max_count_b) = kmc_databases[1]

    # make a database of k-mers that appear only in haplotype A & dump
    print("\033[92mFinding k-mers unique to haplotype A...\033[0m", file=sys.stderr)
    hap_a_only_db = run_kmc_subtract(kmc_db_a, kmc_db_b, args.path_to_kmc)
    print("\033[92mDumping k-mers unique to haplotype A...\033[0m", file=sys.stderr)
    hap_a_num_kmers = run_kmc_dump(
        hap_a_only_db,
        os.path.join(args.outpath, "hapA_only_kmers.txt"),
        min_count_a,
        max_count_a,
        args.path_to_kmc,
    )

    # make a database of k-mers that appear only in haplotype B & dump
    print("\033[92mFinding k-mers unique to haplotype B...\033[0m", file=sys.stderr)
    hap_b_only_db = run_kmc_subtract(kmc_db_b, kmc_db_a, args.path_to_kmc)
    print("\033[92mDumping k-mers unique to haplotype A...\033[0m", file=sys.stderr)
    hap_b_num_kmers = run_kmc_dump(
        hap_b_only_db,
        os.path.join(args.outpath, "hapB_only_kmers.txt"),
        min_count_b,
        max_count_b,
        args.path_to_kmc,
    )

    print(
        "\n\n\033[94m# of unique k-mers in haplotype A: {}\033[0m".format(
            hap_a_num_kmers
        ),
        file=sys.stderr,
    )
    print(
        "\033[94m# of unique k-mers in haplotype B: {}\033[0m".format(hap_b_num_kmers),
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
