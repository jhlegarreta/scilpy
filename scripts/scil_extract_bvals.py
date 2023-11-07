#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Extract b-values on specific b-value shells for sampling schemes where b-values
of a shell are not all identical. The --tolerance argument needs to be adjusted
to vary the accepted interval around the targetted b-value.

For example, a b-value of 2000 and a tolerance of 20 will resample all
volumes with a b-values from 1980 to 2020 to the value of 2000.

>> scil_resample_bvals.py bvals 0 1000 2000 newbvals --tolerance 20
"""

import argparse
import logging

from dipy.io import read_bvals_bvecs
import numpy as np

from scilpy.io.utils import (add_overwrite_arg, add_verbose_arg,
                             assert_inputs_exist, assert_outputs_exist)
from scilpy.gradients.bvec_bval_tools import identify_shells, extract_bvals_from_list


def _build_arg_parser():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawTextHelpFormatter)

    parser.add_argument('in_bval',
                        help='The b-values in FSL format.')
    parser.add_argument('bvals_to_extract', nargs='+',
                        metavar='bvals-to-extract', type=int,
                        help='The list of b-values to extract. For example '
                             '0 1000 2000.')
    parser.add_argument('out_bval',
                        help='The name of the output b-values.')

    parser.add_argument('--tolerance', '-t',
                        metavar='INT', type=int, default=20,
                        help='The tolerated gap between the b-values to '
                             'extract\nand the actual b-values. '
                             '[%(default)s]')

    add_verbose_arg(parser)
    add_overwrite_arg(parser)

    return parser


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    assert_inputs_exist(parser, args.in_bval)
    assert_outputs_exist(parser, args, args.out_bval)

    bvals, _ = read_bvals_bvecs(args.in_bval, None)

    new_bvals = extract_bvals_from_list(bvals, args.tolerance, args.bvals_to_extract)

    logging.info("new bvals: {}".format(new_bvals))
    new_bvals.reshape((1, len(new_bvals)))
    np.savetxt(args.out_bval, new_bvals, '%d')


if __name__ == "__main__":
    main()
