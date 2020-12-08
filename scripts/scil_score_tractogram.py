#!/usr/bin/env python
# -*- coding: utf-8 -*-

import argparse

from dipy.io.stateful_tractogram import Space, Origin, StatefulTractogram
from dipy.io.streamline import save_tractogram
from dipy.tracking.utils import length

import json
import itertools
import nibabel as nib
from nibabel.streamlines import ArraySequence
import numpy as np
import os
import pprint
from sklearn.cluster import KMeans

from scilpy.image.operations import intersection, difference
from scilpy.io.streamlines import load_tractogram_with_reference
from scilpy.io.utils import (add_overwrite_arg,
                             assert_inputs_exist,
                             assert_outputs_exist,
                             add_verbose_arg,
                             add_json_args,
                             add_reference_arg,
                             assert_output_dirs_exist_and_empty)
from scilpy.segment.streamlines import filter_grid_roi
from scilpy.tractanalysis.reproducibility_measures \
    import (compute_dice_voxel, get_endpoints_density_map)
from scilpy.tractanalysis.features import remove_loops_and_sharp_turns
from scilpy.tractanalysis.streamlines_metrics import compute_tract_counts_map
from scilpy.utils.filenames import split_name_with_nii
from scilpy.utils.streamlines import (perform_streamlines_operation,
                                      difference)

from time import time


def _build_arg_parser():
    p = argparse.ArgumentParser(
        description="Scores input tractogram overall and bundlewise. Outputs \
            a results.json containing a full report and splits the input \
            tractogram into resulting .trk : *_tc.trk, *_fc.trk, nc.trk and \
            *_wpc.trk, where * is the current bundle.\n \
            Definitions: tc: true connections, streamlines joining a \
            correct combination of ROIs. \n fc: false connections, \
            streamlines joining an incorrect combination of ROIs.\n \
            nc: no connections, streamlines not joining two ROIs.\n \
            wpc: wrong path connections, streamlines that go outside of \
            the ground truth mask, joining a correct combination of ROIs.\n \
            Bundle overlap : ground truth voxels containing tc streamline(s). \
            Either input gt_endpoints or gt_heads and gt_tails. Ground truth \
            and ROIs must be in the same order i.e. groundTruth1.nii.gz ... \
            groundTruthN.nii.gz --gt_tails tail1.nii.gz ... tailN.nii.gz \
            --gt_heads head1.nii.gz ... headN.nii.gz",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

    p.add_argument('in_tractogram',
                   help="Input tractogram to score(.trk)")
    p.add_argument('gt_bundles', nargs='+',
                   help="Bundles ground truth(.trk, .nii or .nii.gz).")
    p.add_argument('--gt_endpoints', nargs='+',
                   help="Bundles endpoints, both bundle's ROIs\
                       (.nii or .nii.gz).")
    p.add_argument('--gt_tails', nargs='+',
                   help="Bundles tails, bundle's first ROI(.nii or .nii.gz).")
    p.add_argument('--gt_heads', nargs='+',
                   help="Bundles heads, bundle's second ROI(.nii or .nii.gz).")

    # p.add_argument('--dilate_endpoints', metavar='NB_PASS',
    #                help='heuristic')

    p.add_argument('--gt_config', metavar='FILE',
                   help=".json dict to specify bundles streamlines min, \
                    max length and max angles.")
    p.add_argument('--out_dir', default='gt_out/',
                   help="Output directory")

    p.add_argument('--wrong_path_as_separate', action='store_true',
                   help="Separates streamlines that go outside of the ground \
                        truth mask from true connections, outputs as \
                        *_wpc.trk.")

    add_reference_arg(p)
    add_overwrite_arg(p)
    add_json_args(p)

    return p


def find_tc_pos(tc_filenames, filename):
    if filename in tc_filenames:
        return True, tc_filenames.index(filename)
    return False, None


def get_binary_maps(streamlines, dimensions, sft):
    if not len(streamlines):
        return np.zeros(dimensions), np.zeros(dimensions)
    elif len(streamlines) == 1:
        streamlines = [streamlines]
    tmp_sft = StatefulTractogram.from_sft(streamlines, sft)
    tmp_sft.to_vox()
    tmp_sft.to_corner()
    tmp_sft.remove_invalid_streamlines()

    if len(tmp_sft) == 1:
        return np.zeros(dimensions), np.zeros(dimensions)

    bundles_voxels = compute_tract_counts_map(tmp_sft.streamlines,
                                              dimensions).astype(np.int16)

    endpoints_voxels = get_endpoints_density_map(tmp_sft.streamlines,
                                                 dimensions).astype(np.int16)

    bundles_voxels[bundles_voxels > 0] = 1
    endpoints_voxels[endpoints_voxels > 0] = 1

    return bundles_voxels, endpoints_voxels


def identify_overlapping_roi(mask_1, mask_2, overlapping_roi):
    if not os.path.isfile(mask_1):
        raise ValueError('Input file {} does not exist.'.format(mask_1))
    roi_1 = nib.load(mask_1)
    roi1 = roi_1.get_fdata(dtype=np.float64)

    if not os.path.isfile(mask_2):
        raise ValueError('Input file {} does not exist.'.format(mask_2))
    roi_2 = nib.load(mask_2)
    roi2 = roi_2.get_fdata(dtype=np.float64)

    rois = [roi1, roi2]
    overlap = intersection(rois, roi_1)
    nb_voxels = np.count_nonzero(overlap)

    if nb_voxels > 0:
        overlapping_roi.append((mask_1, mask_2))
        overlapping_roi.append((mask_2, mask_1))


def remove_duplicate_streamlines(sft, fc_streamlines, roi1_name, roi2_name):
    roi1 = nib.load(roi1_name).get_fdata().astype(np.int16)
    roi2 = nib.load(roi2_name).get_fdata().astype(np.int16)
    tmp_sft, _ = filter_grid_roi(sft, roi1, 'either_end', False)
    tmp_sft, _ = filter_grid_roi(tmp_sft, roi2, 'either_end', False)
    duplicate_streamlines = tmp_sft.streamlines
    fc_streamlines, _ = perform_streamlines_operation(
        difference, [fc_streamlines, duplicate_streamlines], precision=0)
    return fc_streamlines


def split_heads_tails_kmeans(data):
    X = np.argwhere(data)
    k_means = KMeans(n_clusters=2).fit(X)
    mask_1 = np.zeros(data.shape)
    mask_2 = np.zeros(data.shape)

    mask_1[tuple(X[np.where(k_means.labels_ == 0)].T)] = 1
    mask_2[tuple(X[np.where(k_means.labels_ == 1)].T)] = 1

    return mask_1, mask_2


def main():
    parser = _build_arg_parser()
    args = parser.parse_args()
    assert_output_dirs_exist_and_empty(parser, args, args.out_dir,
                                       create_dir=True)

    if (args.gt_tails and not args.gt_heads) \
            or (args.gt_heads and not args.gt_tails):
        parser.error('Both gt_heads and gt_tails are needed.')
    if not args.gt_endpoints and (not args.gt_tails and not args.gt_heads):
        parser.error('Either input gt_endpoints or gt_heads and gt_tails.')

    sft = load_tractogram_with_reference(parser, args, args.in_tractogram)
    gt_bundle_masks = []
    gt_bundle_inv_masks = []

    for gt_bundle in args.gt_bundles:

        # Support ground truth as streamlines or masks
        # Will be converted to binary masks immediately
        _, ext = split_name_with_nii(gt_bundle)
        if ext in ['.gz', '.nii.gz']:
            gt_img = nib.load(gt_bundle)
            gt_mask = gt_img.get_fdata().astype(np.int16)
            affine = gt_img.affine
            dimensions = gt_mask.shape
        else:
            gt_sft = load_tractogram_with_reference(parser, args, gt_bundle)
            gt_sft.remove_invalid_streamlines()
            gt_sft.to_vox()
            gt_sft.to_corner()
            affine, dimensions, _, _ = gt_sft.space_attributes
            gt_mask = compute_tract_counts_map(gt_sft.streamlines,
                                               dimensions).astype(np.int16)
        gt_inv_mask = np.zeros(dimensions, dtype=np.int16)
        gt_inv_mask[gt_mask == 0] = 1
        gt_mask[gt_mask > 0] = 1
        gt_bundle_masks.append(gt_mask)
        gt_bundle_inv_masks.append(gt_inv_mask)

    # If endpoints without heads/tails are loaded, split them and continue
    # normally after. Q/C of the output is important
    if args.gt_endpoints:
        tails = []
        heads = []
        for mask_filename in args.gt_endpoints:
            mask = nib.load(mask_filename).get_fdata().astype(np.int16)
            head, tail = split_heads_tails_kmeans(mask)

            basename = os.path.basename(
                split_name_with_nii(mask_filename)[0])
            tail_filename = os.path.join(
                args.out_dir, '{}_tail.nii.gz'.format(basename))
            head_filename = os.path.join(
                args.out_dir, '{}_head.nii.gz'.format(basename))
            nib.save(nib.Nifti1Image(head, affine), head_filename)
            nib.save(nib.Nifti1Image(tail, affine), tail_filename)

            tails.append(tail_filename)
            heads.append(head_filename)

        args.gt_tails, args.gt_heads = tails, heads
        args.gt_endpoints = None

    # Load the endpoints heads/tails, keep the correct combinations
    # separately from all the possible combinations
    tc_data = []
    all_data = []
    fused_masks = np.zeros(dimensions)
    tc_filenames = list(zip(args.gt_tails, args.gt_heads))

    indices = []
    for mask_1_filename, mask_2_filename in tc_filenames:
        mask_1 = nib.load(mask_1_filename).get_fdata().astype(np.int16)
        mask_2 = nib.load(mask_2_filename).get_fdata().astype(np.int16)
        _, loops_1_ind = filter_grid_roi(sft, mask_1, 'both_ends', False)
        _, loops_2_ind = filter_grid_roi(sft, mask_2, 'both_ends', False)
        indices.extend(loops_1_ind)
        indices.extend(loops_2_ind)
        # if args.dilate_endpoints:
        # That would be done here.

        fused_masks += mask_1 + mask_2
        tc_data.append((mask_1, mask_2))
        all_data.extend([mask_1, mask_2])
    fused_masks[fused_masks > 0] = 1

    indices = np.unique(indices).tolist()
    no_conn_streamlines = []
    no_conn_streamlines.extend(sft.streamlines[indices])

    overlapping_roi = []
    all_rois = args.gt_heads + args.gt_tails
    for roi1, roi2 in itertools.combinations(all_rois, 2):
        identify_overlapping_roi(roi1, roi2, overlapping_roi)

    if args.gt_config:
        with open(args.gt_config, 'r') as json_file:
            length_dict = json.load(json_file)

    tc_streamlines_list = []
    fc_streamlines_list = []
    wpc_streamlines_list = []

    # Again keep the keep the correct combinations
    comb_filename = list(itertools.combinations(
        itertools.chain(*zip(args.gt_tails, args.gt_heads)), r=2))
    comb_data = list(itertools.combinations(all_data, r=2))

    # Go through all the possible combinations of endpoints masks
    for i, roi in enumerate(comb_data):
        timer = time()
        tmp_sft, _ = filter_grid_roi(sft, roi[0], 'either_end', False)
        tmp_sft, _ = filter_grid_roi(tmp_sft, roi[1], 'either_end', False)
        streamlines = tmp_sft.streamlines

        # Different processing for true connections and false connections
        is_tc, tc_pos = find_tc_pos(tc_filenames, comb_filename[i])
        if is_tc:
            tc_streamlines = streamlines
            fc_streamlines = []

            # Config file for each 'bundle'
            # Loops => no connection (nc)
            # Length => false connection (fc)
            if args.gt_config:
                min_len, max_len = \
                    length_dict[args.gt_bundles[tc_pos]]['length']

                lengths = np.array(list(length(streamlines)))
                valid_min_length_mask = lengths > min_len
                valid_max_length_mask = lengths < max_len
                valid_length_mask = np.logical_and(valid_min_length_mask,
                                                   valid_max_length_mask)
                streamlines = ArraySequence(streamlines)

                val_len_streamlines = streamlines[valid_length_mask]
                fc_streamlines = streamlines[~valid_length_mask]

                angle = length_dict[args.gt_bundles[tc_pos]]['angle']
                tc_streamlines, loops = remove_loops_and_sharp_turns(
                    val_len_streamlines, angle)

                if loops:
                    no_conn_streamlines.extend(loops)

            else:
                tc_streamlines = streamlines
                fc_streamlines = []

            # Streamlines getting out of the bundle mask can be considered
            # separately as wrong path connection (wpc)
            if args.wrong_path_as_separate:
                tmp_sft = StatefulTractogram.from_sft(tc_streamlines, sft)
                wpc_stf, _ = filter_grid_roi(tmp_sft,
                                             gt_bundle_inv_masks[tc_pos],
                                             'any', False)
                wpc_streamlines = wpc_stf.streamlines
                tc_streamlines, _ = perform_streamlines_operation(
                    difference, [tc_streamlines, wpc_streamlines], precision=0)
            else:
                wpc_streamlines = []
        else:
            tc_streamlines = []
            wpc_streamlines = []
            fc_streamlines = streamlines

            roi_comb = comb_filename[i]
            roi1_overlap_list = []
            roi2_overlap_list = []

            # Exclude ROIs combination if both are overlapping each other
            if roi_comb in overlapping_roi:
                fc_streamlines = []
            # Exclude tc streamlines from fc streamlines, if one or both ROI
            # overlap another one. It eliminates the chance to get streamlines
            # that correctly classify as tc and incorrectly as fc.
            elif roi_comb[0] in itertools.chain(*overlapping_roi) or \
                    roi_comb[1] in itertools.chain(*overlapping_roi):

                roi1_overlap_list = [item for item in overlapping_roi
                                     if item[0] == roi_comb[0]]
                roi1_overlap_list.insert(0, (0, roi_comb[0]))

                roi2_overlap_list = [item for item in overlapping_roi
                                     if item[0] == roi_comb[1]]
                roi2_overlap_list.insert(0, (0, roi_comb[1]))

                for roi1_name in roi1_overlap_list:
                    for roi2_name in roi2_overlap_list:
                        is_tp1, _ = find_tc_pos(tc_filenames,
                                                (roi1_name[1], roi2_name[1]))
                        if is_tp1:
                            fc_streamlines = \
                                remove_duplicate_streamlines(sft,
                                                             fc_streamlines,
                                                             roi1_name[1],
                                                             roi2_name[1])

                        is_tp2, _ = find_tc_pos(tc_filenames,
                                                (roi2_name[1], roi1_name[1]))
                        if is_tp2:
                            fc_streamlines = \
                                remove_duplicate_streamlines(sft,
                                                             fc_streamlines,
                                                             roi2_name[1],
                                                             roi1_name[1])

        tc_streamlines_list.append(tc_streamlines)
        fc_streamlines_list.append(fc_streamlines)
        wpc_streamlines_list.append(wpc_streamlines)

        # Automatically generate filename for Q/C
        prefix_1 = os.path.basename(comb_filename[i][0])
        prefix_1, _ = split_name_with_nii(prefix_1)
        prefix_2 = os.path.basename(comb_filename[i][1])
        prefix_2, _ = split_name_with_nii(prefix_2)
        _, ext = os.path.splitext(args.in_tractogram)

        if is_tc:
            tc_sft = StatefulTractogram.from_sft(tc_streamlines, sft)
            save_tractogram(tc_sft, os.path.join(
                args.out_dir, '{}_{}_tc{}'.format(prefix_1, prefix_2, ext)))

            if args.wrong_path_as_separate:
                wpc_sft = StatefulTractogram.from_sft(wpc_streamlines, sft)
                if len(wpc_sft) > 0:
                    save_tractogram(wpc_sft,
                                    os.path.join(args.out_dir,
                                                 '{}_{}_wpc{}'.format(prefix_1,
                                                                      prefix_2,
                                                                      ext)))

        fc_sft = StatefulTractogram.from_sft(fc_streamlines, sft)
        if len(fc_sft) > 0:
            if is_tc:
                save_tractogram(fc_sft, os.path.join(
                    args.out_dir, '{}_{}_fc_inv{}'.format(prefix_1,
                                                          prefix_2, ext)))
            else:
                save_tractogram(fc_sft, os.path.join(
                    args.out_dir, '{}_{}_fc{}'.format(prefix_1,
                                                      prefix_2, ext)))

    no_conn_sft, _ = filter_grid_roi(sft, fused_masks,
                                     'either_end', True)
    no_conn_streamlines = no_conn_sft.streamlines
    tmp_sft, _ = filter_grid_roi(sft, fused_masks,
                                 'either_end', False)
    one_conn_sft, _ = filter_grid_roi(tmp_sft, fused_masks,
                                      'both_ends', True)
    one_conn_streamlines = one_conn_sft.streamlines
    no_conn_streamlines.extend(no_conn_streamlines)
    no_conn_streamlines.extend(one_conn_streamlines)

    final_results = {}
    no_conn_sft = StatefulTractogram.from_sft(no_conn_streamlines, sft)
    save_tractogram(no_conn_sft, os.path.join(
        args.out_dir, 'nc{}'.format(ext)))

    # Total number of streamlines for each category
    # and statistic that are not 'bundle-wise'
    tc_streamlines_count = len(list(itertools.chain(*tc_streamlines_list)))
    fc_streamlines_count = len(list(itertools.chain(*fc_streamlines_list)))

    if args.wrong_path_as_separate:
        wpc_streamlines_count = len(
            list(itertools.chain(*wpc_streamlines_list)))
    else:
        wpc_streamlines_count = 0

    nc_streamlines_count = len(no_conn_streamlines)
    total_count = tc_streamlines_count + fc_streamlines_count + \
        wpc_streamlines_count + nc_streamlines_count

    final_results['tractogram_filename'] = str(args.in_tractogram)
    final_results['tractogram_overlap'] = 0.0
    final_results['tc_streamlines'] = tc_streamlines_count
    final_results['fc_streamlines'] = fc_streamlines_count
    final_results['nc_streamlines'] = nc_streamlines_count

    final_results['tc_bundle'] = len([x for x in tc_streamlines_list if x])
    final_results['fc_bundle'] = len([x for x in fc_streamlines_list if x])

    final_results['tc_streamlines_ratio'] = tc_streamlines_count / total_count
    final_results['fc_streamlines_ratio'] = fc_streamlines_count / total_count
    final_results['nc_streamlines_ratio'] = nc_streamlines_count / total_count

    if args.wrong_path_as_separate:
        final_results['wpc_streamlines'] = wpc_streamlines_count
        final_results['wpc_streamlines_ratio'] = \
            wpc_streamlines_count / total_count
        final_results['wpc_bundle'] = len(
            [x for x in wpc_streamlines_list if x])

    final_results['total_streamlines'] = total_count
    final_results["bundle_wise"] = {}
    final_results["bundle_wise"]["true_connections"] = {}
    final_results["bundle_wise"]["false_connections"] = {}
    tractogram_overlap = 0.0

    # Bundle-wise statistics, useful for more complex phantom
    for i, filename in enumerate(comb_filename):
        timer = time()
        is_tp, tc_pos = find_tc_pos(tc_filenames, filename)
        current_tc_streamlines = tc_streamlines_list[i]
        current_tc_voxels, current_tc_endpoints_voxels = get_binary_maps(
            current_tc_streamlines, dimensions, sft)

        current_fc_streamlines = fc_streamlines_list[i]
        current_fc_voxels, _ = get_binary_maps(
            current_fc_streamlines, dimensions, sft)

        if args.wrong_path_as_separate:
            current_wpc_streamlines = wpc_streamlines_list[i]
            current_wpc_voxels, _ = get_binary_maps(
                current_wpc_streamlines, dimensions, sft)

        tmp_dict = {}
        if is_tp:
            tmp_dict['tc_streamlines'] = len(current_tc_streamlines)
            tmp_dict['tc_inv_streamlines'] = len(current_fc_streamlines)

            tmp_dict['tc_dice'] = compute_dice_voxel(gt_bundle_masks[tc_pos],
                                                     current_tc_voxels)[0]

            bundle_overlap = gt_bundle_masks[tc_pos] * current_tc_voxels
            bundle_overreach = np.zeros(dimensions)
            bundle_overreach[np.where(
                (gt_bundle_masks[tc_pos] == 0) & (current_fc_voxels > 1))] = 1
            bundle_lacking = np.zeros(dimensions)
            bundle_lacking[np.where(
                (gt_bundle_masks[tc_pos] == 1) & (current_tc_voxels == 0))] = 1

            tmp_dict['tc_bundle_overlap'] = np.count_nonzero(bundle_overlap)
            tmp_dict['tc_bundle_overreach'] = \
                np.count_nonzero(bundle_overreach)
            tmp_dict['tc_bundle_lacking'] = np.count_nonzero(bundle_lacking)
            tmp_dict['tc_bundle_overlap_PCT'] = \
                tmp_dict['tc_bundle_overlap'] / \
                (tmp_dict['tc_bundle_overlap'] +
                    tmp_dict['tc_bundle_lacking'])
            tractogram_overlap += tmp_dict['tc_bundle_overlap_PCT']

            endpoints_overlap = \
                gt_bundle_masks[tc_pos] * current_tc_endpoints_voxels
            endpoints_overreach = np.zeros(dimensions)
            endpoints_overreach[np.where(
                (gt_bundle_masks[tc_pos] == 0) & (current_fc_voxels > 1))] = 1
            tmp_dict['tc_endpoints_overlap'] = np.count_nonzero(
                endpoints_overlap)

            if args.wrong_path_as_separate:
                tmp_dict['wpc_streamlines'] = len(current_wpc_streamlines)
                tmp_dict['wpc_dice'] = \
                    compute_dice_voxel(gt_bundle_masks[tc_pos],
                                       current_wpc_voxels)[0]

            final_results["bundle_wise"]["true_connections"][str(filename)] = \
                tmp_dict

        elif len(current_fc_streamlines):
            tmp_dict['fc_streamlines'] = len(current_fc_streamlines)
            tmp_dict['fc_voxels'] = np.count_nonzero(current_fc_voxels)

            final_results["bundle_wise"]["false_connections"][str(filename)] =\
                tmp_dict

    final_results['tractogram_overlap'] = \
        tractogram_overlap / len(gt_bundle_masks)

    with open(os.path.join(args.out_dir, 'results.json'), 'w') as f:
        json.dump(final_results, f,
                  indent=args.indent,
                  sort_keys=args.sort_keys)


if __name__ == '__main__':
    main()
