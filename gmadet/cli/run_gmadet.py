#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
Author: David Corre, Orsay, France, corre@lal.in2p3.fr

"""

import sys
import subprocess
import glob
import math
import shutil
import os
import argparse
import warnings

from gmadet.phot_calibration import phot_calib
from gmadet.utils import (
    load_config,
    clean_folder,
    cut_image,
    list_files,
    cp_p,
    mv_p,
    mkdir_p,
    make_results_dir,
    clean_outputs,
    getpath,
    getTel,
)
from gmadet.sanitise import sanitise_fits
from gmadet.remove_cosmics import run_lacosmic
from gmadet.astrometry import astrometric_calib
from gmadet.psfex import psfex
from gmadet.sources_extraction import run_sextractor, filter_sources, convert_xy_radec
from gmadet.substraction import substraction
from gmadet.background import bkg_estimation
from gmadet.crossmatch import catalogs, moving_objects
from gmadet.database import send_data2DB
from gmadet.filter_candidates import filter_candidates

from astropy.io import ascii, fits
from astropy.table import vstack, Table, Column

from astropy import wcs
from astropy.coordinates import SkyCoord
from astropy import units as u

from copy import deepcopy

warnings.simplefilter(action="ignore", category=FutureWarning)


def main():

    path_gmadet = getpath()

    telescope_list = getTel()

    parser = argparse.ArgumentParser(
        usage="usage: %(prog)s data [data2 ... dataN] [options]",
        description="Finding unknown objects in astronomical images.",
    )

    parser.add_argument(
        "--results",
        dest="path_results",
        required=False,
        type=str,
        default="gmadet_results",
        help="Base path to store the results. " "(Default: gmadet_results)",
    )

    parser.add_argument(
        "--keep-old",
        "--keep",
        dest="keep",
        required=False,
        action="store_true",
        help="Keep previous results",
    )

    parser.add_argument(
        "--skip-processed",
        "--skip",
        dest="skip",
        required=False,
        action="store_true",
        help="Skip already processed files",
    )

    parser.add_argument(
        "--preprocess",
        dest="preprocess",
        required=False,
        type=str,
        default=None,
        help="Pre-process the image using external program before analysing. "
        "The program should accept two positional arguments - original "
        "filename and new one. (Default: just copy the image)",
    )

    parser.add_argument(
        "--fwhm",
        dest="FWHM",
        required=False,
        default="psfex",
        help="Typical telescope FWHM. " "(Default: use psfex to estimate FWHM)",
    )

    parser.add_argument(
        "--radius-crossmatch",
        dest="radius_crossmatch",
        required=False,
        type=float,
        default=3.0,
        help="Radius to use for crossmatching, in pixels. " "(Default: 3.0 pixels)",
    )

    parser.add_argument(
        "--threshold",
        dest="threshold",
        required=False,
        default=4.0,
        type=float,
        help="Consider only sources above this threshold. " "(Default: 4.0)",
    )

    parser.add_argument(
        "--detect",
        dest="soft",
        required=False,
        choices=["sextractor"],
        default="sextractor",
        type=str,
        help="Software to use for detecting sources.\n (Default: sextractor)",
    )

    parser.add_argument(
        "--conv-filter",
        dest="convFilter",
        required=False,
        default="default",
        type=str,
        help="Corresponds to FILTER_NAME keyword for sextractor "
        "(without .conv)."
        "\nDifferent filter available listed here: %s" % path_gmadet
        + "/config/conv_kernels/"
        "\n(Default: default)",
    )

    parser.add_argument(
        "--telescope",
        dest="telescope",
        choices=telescope_list,
        required=True,
        type=str,
        help="Alias for the available telescopes.",
    )

    parser.add_argument(
        "--threads",
        dest="threads",
        required=False,
        default=4,
        type=int,
        help="Number of threads to use. " "(Default: 4)",
    )
    parser.add_argument(
        "--quadrants",
        dest="quadrants",
        required=False,
        default=1,
        type=int,
        help="Number of quadrants the image is divided. " "(Default: 1)",
    )

    parser.add_argument(
        "--astrometry",
        dest="doAstrometry",
        required=False,
        default="scamp",
        choices=["no", "scamp"],
        type=str,
        help="Whether to perform astrometric calibration, with scamp. "
        "(Default: scamp)",
    )

    parser.add_argument(
        "--accuracy",
        dest="accuracy",
        required=False,
        type=float,
        default=0.15,
        help="Astrometric accuracy to reach, in arcseconds. "
        "(Defautl: 0.15 arcseconds)",
    )

    parser.add_argument(
        "--itermax",
        "--iter-max",
        dest="itermax",
        required=False,
        type=float,
        default=5,
        help="Max number of iteration to reach the required accuracy. " "(Default: 5)",
    )

    parser.add_argument(
        "--verbose",
        dest="verbose",
        required=False,
        default="NORMAL",
        choices=["QUIET", "NORMAL", "FULL", "LOG"],
        type=str,
        help="Level of verbose, according to astromatic software. " "(Default: NORMAL)",
    )

    parser.add_argument(
        "--sub",
        dest="doSub",
        required=False,
        type=str,
        help="Whether to perform astrometric calibration, with ps1 images "
        'or user provided reference image. Type "ps1" for PS1 reference '
        "image or provide the path to your reference image.",
    )

    parser.add_argument(
        "--ps1-method",
        dest="ps1_method",
        required=False,
        default="individual",
        choices=["mosaic", "individual"],
        type=str,
        help="When substracting images using Pan-STARRS reference images, "
        "there 2 options, either create a mosaic of all PS1 image and "
        "substract or do the substraction individually for each PS1 "
        "image. In the latter case, your image is cut to match the "
        "PS1 image. (Default: mosaic)",
    )

    parser.add_argument(
        "--mosaic",
        dest="doMosaic",
        action="store_true",
        help="Whether to combine the individual frames into a common mosaic "
        "when `ps1_method` is set to `individual`. (Default: not set)",
    )

    parser.add_argument(
        "--remove-cosmics",
        dest="Remove_cosmics",
        action="store_true",
        help="Whether to remove cosmic rays using lacosmic. " " (Default: not set)",
    )

    parser.add_argument(
        "--sub-bkg",
        dest="sub_bkg",
        action="store_true",
        help="Whether to substract background. (Default: not set)",
    )

    parser.add_argument(
        "--cnn_model",
        dest="cnn_model",
        required=False,
        type=str,
        help="Path to trained CNN model. Defaut: None. ",
    )

    parser.add_argument(
        "--cnn_thres",
        dest="cnn_thres",
        required=False,
        type=float,
        help="Threshold to apply to filter CNN results, between 0 and 1. "
        "Defaut: 0.0. ",
    )

    parser.add_argument(
        "--doCutouts",
        dest="doCutouts",
        action="store_true",
        help="Whether to create cutouts centered on candidates. (Default: not set)",
    )

    parser.add_argument(
        "--cutouts-size",
        dest="cutouts_size",
        required=False,
        type=int,
        default=32,
        help="Size of square array for cutouts other than for CNN. " "Defaut: 100. ",
    )

    parser.add_argument(
        "--cutouts-size_cnn",
        dest="cutouts_size_cnn",
        required=False,
        type=int,
        default=32,
        help="Size of square array for CNN cutouts. Defaut: 32. ",
    )

    parser.add_argument(
        "--cutouts-all",
        dest="cutouts_all",
        action="store_true",
        help="Whether to create cutouts with science, reference and residual "
        "on same plot. (Default: not set)",
    )

    parser.add_argument(
        "--cutouts-fmt",
        dest="cutouts_fmt",
        required=False,
        type=str,
        default="fits",
        choices=["png", "fits"],
        help="File format for cutouts. Defaut: fits. ",
    )

    parser.add_argument(
        "--output-data-level",
        dest="outLevel",
        required=False,
        type=int,
        default=2,
        choices=[0, 1, 2],
        help="Number of output files that are kept after the process. "
        "0: minimum, 2: maximum"
        "(Default: 2)",
    )

    parser.add_argument(
        "--owncloud",
        dest="owncloud_path",
        required=False,
        type=str,
        help="Local path to the owncloud",
    )

    parser.add_argument(
        "--voe",
        dest="VOE_path",
        required=False,
        type=str,
        help="Path/filename of the VOEvent containing the observation plan.",
    )

    # args, filenames = parser.parse_known_args()
    args, filenames = parser.parse_known_args()

    Nb_cuts = (args.quadrants, args.quadrants)

    # Load config files for a given telescope
    config = load_config(args.telescope, args.convFilter)

    filenames, subdirs = list_files(filenames, exclude=args.path_results)

    for raw_filename, subdir in zip(filenames, subdirs):
        filename = make_results_dir(
            raw_filename,
            outputDir=os.path.join(args.path_results, subdir),
            keep=args.keep,
            skip=args.skip,
            copy=False if args.preprocess else True,
        )

        if not filename:
            print("%s is already processed, skipping. \n" % raw_filename)
            continue

        if args.preprocess:
            # We need to call external code what will copy (processed)
            # image to results dir
            print("Pre-processing %s" % raw_filename)
            subprocess.call(args.preprocess.split() + [raw_filename, filename])

            if not os.path.exists(filename):
                print("Pre-processing failed")
                continue

        # If there is simulated_objects.list file alongside the image,
        # let's copy it to the results dir
        if os.path.exists(
            os.path.join(os.path.dirname(raw_filename), "simulated_objects.list")
        ):
            cp_p(
                os.path.join(os.path.dirname(raw_filename), "simulated_objects.list"),
                os.path.join(os.path.dirname(filename), "simulated_objects.list"),
            )
            # Rename the "filename" location in the copied
            # 'simulated_objects.list'
            fname = os.path.join(os.path.dirname(filename), "simulated_objects.list")
            sim_obj = ascii.read(fname)

            newname_list = []
            for i in range(len(sim_obj)):
                newname = os.path.join(
                    os.path.dirname(filename), os.path.split(sim_obj[i]["filename"])[1]
                )
                newname_list.append(os.path.abspath(newname))
            sim_obj["filename"] = newname_list
            sim_obj.write(fname, format="ascii.commented_header", overwrite=True)

        print("Sanitise header and data of %s.\n" % filename)
        sanitise_fits(filename)

        # Cut image into several quadrants if required
        # And create table with filename and quadrant ID
        image_table = cut_image(
            filename, config, Nb_cuts=Nb_cuts, doAstrometry=args.doAstrometry
        )

        if args.Remove_cosmics:
            print("Running lacosmic on %s to remove cosmic rays. \n" % filename)
            # Clean cosmic rays
            # Not using FWHM anymore
            FWHM_list = [None] * len(image_table)
            run_lacosmic(
                image_table["filenames"],
                FWHM_list,
                contrast=5.0,
                cr_threshold=5.0,
                neighbor_threshold=5.0,
                maxiter=4,
                outLevel=args.outLevel,
            )

        if args.sub_bkg:
            # Substract background
            bkg_estimation(
                image_table["filenames"],
                box=(20, 20),
                filter_size=(3, 3),
                bkg_estimator="SExtractor",
                sigma=3.0,
                sigma_lower=None,
                sigma_upper=None,
                maxiters=10,
                outLevel=args.outLevel,
            )

        if args.FWHM == "psfex":
            # Estimate the PSF FWHM for each image/quadrants using psfex
            FWHM_list = psfex(
                image_table["filenames"],
                config,
                verbose=args.verbose,
                outLevel=args.outLevel,
            )
        else:
            FWHM_list = [args.FWHM] * len(image_table)

        if args.doAstrometry != "no":
            astrometric_calib(
                image_table["filenames"],
                config,
                soft=args.doAstrometry,
                verbose=args.verbose,
                accuracy=args.accuracy,
                itermax=args.itermax,
            )

        if args.doSub:
            substracted_files = substraction(
                image_table["filenames"],
                args.doSub,
                config,
                soft="hotpants",
                method=args.ps1_method,
                doMosaic=args.doMosaic,
                verbose=args.verbose,
                outLevel=args.outLevel,
                nb_threads=args.threads,
            )
        else:
            substracted_files = None

        if args.soft == "sextractor":
            run_sextractor(
                image_table["filenames"],
                FWHM_list,
                args.threshold,
                args.telescope,
                config,
                verbose=args.verbose,
                subFiles=substracted_files,
                outLevel=args.outLevel,
                nb_threads=args.threads,
            )

        filter_sources(
            image_table["filenames"],
            args.soft,
            sigma=1,
            subFiles=substracted_files,
        )

        convert_xy_radec(
            image_table["filenames"], soft=args.soft, subFiles=substracted_files
        )

        total_sources = catalogs(
            image_table,
            args.radius_crossmatch,
            Nb_cuts=Nb_cuts,
            subFiles=substracted_files,
            # 4 threads are faster than 8 here so in general divide by 2
            nb_threads=int(args.threads / 2),
        )

        # The radius is used here to crossmatch our sources with
        # catalogs to derive the Zeropoint. Better keep 3 pixels.
        sources_calib, candidates = phot_calib(
            total_sources,
            args.telescope,
            radius=3,
            doPlot=True,
            subFiles=substracted_files,
            # 4 threads are faster than 8 here so in general divide by 2
            nb_threads=int(args.threads / 2),
        )

        candidates = moving_objects(candidates)

        # Apply filter to candidates
        # Remove candidates on the edge
        # Remove candidate depending the FWHM ratio
        # Apply the CNN model
        candidates_filtered = filter_candidates(
            candidates,
            FWHM_ratio_lower=0.5,
            FWHM_ratio_upper=5.0,
            CNN_model=args.cnn_model,
            CNN_thres=args.cnn_thres,
            makecutout=args.doCutouts,
            size=args.cutouts_size,
            size_cnn=args.cutouts_size_cnn,
            fmt=args.cutouts_fmt,
            outLevel=1,
            nb_threads=args.threads,
            combined=args.cutouts_all,
        )

        # If both arguments VOE_path and owncloud_path are provided
        # Send candidates to database
        # Set the tile_id corresponding to your tile by hand at the moment
        if args.VOE_path and args.owncloud_path:
            send_data2DB(
                filename,
                candidates_filtered,
                Nb_cuts,
                args.owncloud_path,
                args.VOE_path,
                "utilsDB/usrpwd.json",
                debug=True,
                subFiles=substracted_files,
            )

        # clean output files
        clean_outputs(image_table["filenames"], args.outLevel)


if __name__ == "__main__":
    main()
