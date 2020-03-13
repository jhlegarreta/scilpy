#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Compute the mean Fiber Response Function from a set of individually
computed Response Functions.
"""

from __future__ import division, print_function

import argparse
import logging

import numpy as np

from scilpy.io.utils import (
    add_overwrite_arg, assert_inputs_exist, assert_outputs_exist, load_frf,
    save_frf)


def _build_arg_parser():
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawTextHelpFormatter)

    p.add_argument('frf_files', metavar='list', nargs='+',
                   help='List of FRF filepaths.')
    p.add_argument('mean_frf', metavar='file',
                   help='Path of the output mean FRF file.')

    add_overwrite_arg(p)

    return p


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)

    assert_inputs_exist(parser, args.frf_files)
    assert_outputs_exist(parser, args, args.mean_frf)

    all_frfs = np.zeros((len(args.frf_files), 4))

    for idx, frf_file in enumerate(args.frf_files):

        frf = load_frf(frf_file)

        all_frfs[idx] = frf

    final_frf = np.mean(all_frfs, axis=0)

    save_frf(args.mean_frf, final_frf)


if __name__ == "__main__":
    main()
