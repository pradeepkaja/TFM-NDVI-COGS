"""
Utilities to download Sentinel-2 COGS from S3

Original package: https://github.com/kikocorreoso/s2froms3
GNU Affero General Public License v3.0
"""

import json
import os
import datetime as dt
from typing import Union, Iterable, List, Optional
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, wait
from itertools import cycle
from time import sleep
import boto3
import threading

import mgrs  # type: ignore
import s3fs  # type: ignore

from .utils import _iter_dates
from .products import Properties

CPU_COUNT = os.cpu_count()

def list_subdirs(client, bucket: str, prefix: str) -> List[str]:
    """
    Lists subdirectories (prefixes) under a given prefix using the paginator.
    """
    subdirs = []
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix, Delimiter="/"):
        for cp in page.get("CommonPrefixes", []):
            subdirs.append(cp["Prefix"])
    return subdirs

def get_scene_list(
    lon: float,
    lat: float,
    start_date: Union[dt.date, dt.datetime],
    end_date: Union[dt.date, dt.datetime],
    what: Union[str, Iterable[str]],
    cloud_cover_le: float = 50,
    use_ssl: bool = True,
    also: Optional[List[str]] = None
) -> List[str]:
    """
    Returns a list of strings, where each string is the full S3 path (including the bucket)
    of a valid file for a given location, using boto3 to explore folders and files.

    Parameters
    ----------
    lon : float
        Longitude of interest.
    lat : float
        Latitude of interest.
    start_date : datetime.date or datetime.datetime
        Start date for searching images.
    end_date : datetime.date or datetime.datetime
        End date for searching images.
    what : str or array_like
        Product(s) to download. E.g.: 'TCI', 'B01', 'B02', ...
    cloud_cover_le : float, optional
        Maximum allowed cloud coverage percentage (default 50).
    use_ssl : bool, optional
        Indicates whether SSL is used for the connection.
    also : list or None, optional
        List of adjacent directions (e.g., 'N', 'NE', etc.).

    Returns
    -------
    List[str]
        List of strings, each one is the full S3 path of a valid file.
    """
    _also = {
        "N": {"x": 0, "y": 150_000},
        "NE": {"x": 150_000, "y": 150_000},
        "E": {"x": 150_000, "y": 0},
        "SE": {"x": 150_000, "y": -150_000},
        "S": {"x": 0, "y": -150_000},
        "SW": {"x": -150_000, "y": -150_000},
        "W": {"x": -150_000, "y": 0},
        "NW": {"x": -150_000, "y": 150_000},
    }
    if start_date > end_date:
        raise ValueError("`start_date` must be less than or equal to `end_date`")
    if isinstance(what, str):
        what = [what]
    for w in what:
        if w.upper() not in [item.value for item in Properties]:
            raise ValueError(f"{w} is not a valid product")
    
    # Configure boto3
    bucket = "sentinel-cogs"
    client = boto3.client("s3", use_ssl=use_ssl)
    
    # Normalize dates to date objects
    start_date = dt.date(start_date.year, start_date.month, start_date.day)
    end_date = dt.date(end_date.year, end_date.month, end_date.day)
    
    results = []  # List to store each valid path
    results_lock = threading.Lock()
    
    # Get the MGRS coordinate for the main tile
    m = mgrs.MGRS()
    coord = m.toMGRS(lat, lon, MGRSPrecision=0)
    number, a, b = coord[:-3], coord[-3:-2], coord[-2:]
    
    futures = []
    with ThreadPoolExecutor() as executor:
        
        def check_tile(tile_prefix: str):
            """
            Processes a tile (directory) and, if it meets the criteria, adds each file
            (as string) to the results list, including the bucket.
            """
            try:
                name = tile_prefix.rstrip("/").split("/")[-1]
                info_key = tile_prefix + name + ".json"
                response = client.get_object(Bucket=bucket, Key=info_key)
                info = json.loads(response["Body"].read())
                date_str = name.split("_")[2]
                cc = info["properties"]["eo:cloud_cover"]
                date = dt.datetime.strptime(date_str, "%Y%m%d").date()
                if cloud_cover_le >= cc and start_date <= date <= end_date:
                    for w in what:
                        # Build full path including the bucket
                        valid_path = f"{bucket}/" + tile_prefix + f"{w}.tif"
                        with results_lock:
                            results.append(valid_path)
            except Exception as e:
                print(f"Error processing tile {tile_prefix}: {e}")
        
        def check_package(prefix: str):
            """
            Lists subdirectories under the given prefix and runs check_tile for each one.
            """
            try:
                subdirs = list_subdirs(client, bucket, prefix)
                for tile_prefix in subdirs:
                    futures.append(executor.submit(check_tile, tile_prefix))
            except Exception as e:
                print(f"Error listing prefix {prefix}: {e}")
        
        # Process main tile for each year and month combination
        for yy, mm in _iter_dates(start_date, end_date):
            prefix = f"sentinel-s2-l2a-cogs/{number}/{a}/{b}/{yy}/{mm}/"
            futures.append(executor.submit(check_package, prefix))
        
        # Process adjacent tiles if specified
        if also is None:
            also = []
        for al in also:
            al = al.upper()
            if al not in _also:
                raise ValueError(f'"{al}" is not a valid value for `also`')
            z, hem, x, y = m.MGRSToUTM(coord)
            x += _also[al]["x"]
            y += _also[al]["y"]
            _coord = m.UTMToMGRS(z, hem, x, y, MGRSPrecision=0)
            number_adj, a_adj, b_adj = _coord[:-3], _coord[-3:-2], _coord[-2:]
            for yy, mm in _iter_dates(start_date, end_date):
                prefix = f"sentinel-s2-l2a-cogs/{number_adj}/{a_adj}/{b_adj}/{yy}/{mm}/"
                try:
                    subdirs = list_subdirs(client, bucket, prefix)
                    for tile_prefix in subdirs:
                        def process_adjacent(tp=tile_prefix):
                            try:
                                name = tp.rstrip("/").split("/")[-1]
                                info_key = tp + name + ".json"
                                response = client.get_object(Bucket=bucket, Key=info_key)
                                info = json.loads(response["Body"].read())
                                date_str = name.split("_")[2]
                                cc = info["properties"]["eo:cloud_cover"]
                                date = dt.datetime.strptime(date_str, "%Y%m%d").date()
                                if cloud_cover_le >= cc and start_date <= date <= end_date:
                                    for w in what:
                                        valid_path = f"{bucket}/" + tp + f"{w}.tif"
                                        with results_lock:
                                            results.append(valid_path)
                            except Exception as e:
                                print(f"Error processing adjacent tile {tp}: {e}")
                        futures.append(executor.submit(process_adjacent))
                except Exception as e:
                    print(f"Error listing adjacent prefix {prefix}: {e}")
        
        # Wait for all stasks to finish.
        wait(futures)
    
    return results

def download_S2(
    scenes: List[str],
    folder: Union[str, Path] = Path.home(),
    workers: int = CPU_COUNT,
) -> List[str]:
    """Download Sentinel 2 COG (Cloud Optimized GeoTiff) images from Amazon S3.

    The dataset on AWS contains all of the scenes in the original Sentinel-2
    Public Dataset and will grow as that does. L2A data are available from
    April 2017 over wider Europe region and globally since December  2018. Read
    more at the url https://registry.opendata.aws/sentinel-2-l2a-cogs/

    Parameters
    ----------
    packages: list
        List of tuples that contains what to download
    folder: str or Path
        Where to download the data. The folder must exist. Default value is
        the home directory of the user.
    workers: int
        Number of parallel downloads using threading. Default value is 4.

    Returns
    -------
    list
        A list with the paths of the downloaded files.
    """
    rpaths = []
    lpaths = []

    for s in scenes:
        for what in s:
            rpaths.append(what)
            path = what.rsplit('/', 2)
            lpath = f'{folder}/{path[1]}_{path[2]}'
            lpaths.append(lpath)

    s3 = boto3.client('s3')

    def get_file(rpath: Union[str, Path], lpath: Union[str, Path]) -> None:
        bucket, obj = rpath.split('/', 1)
        s3.download_file(bucket, obj, lpath)

    executor = ThreadPoolExecutor(max_workers=workers)
    ex = [executor.submit(get_file, rp, lp) for rp, lp in zip(rpaths, lpaths)]
    cy = cycle(r"-\|/")
    while not all([exx.done() for exx in ex]):
        print("Downloading data " + next(cy), end="\r")
        sleep(0.1)

    return sorted(lpaths)