import tarfile
from pathlib import Path
from typing import Optional, Union

import numpy as np
import pandas as pd
from data_downloader import downloader
from faninsar.query import BoundingBox
from tqdm.auto import tqdm

from .parse_email import GACOSEmail


class Downloader:
    def __init__(
        self,
        url_file: Union[Path, str],
        output_dir: Union[Path, str],
        tar_gz_dir: Optional[Union[Path, str]] = None,
        keep_original: bool = False,
        times: Optional[Union[float, list[float]]] = None,
        bounds: Optional[tuple[float, float, float, float]] = None,
    ) -> None:
        """Initialize Downloader class

        Parameters
        ----------
        url_file : Union[Path, str]
            Path to file containing URLs that created by :meth:`GACOSEmail.retrieve_gacos_urls`
        output_dir : Union[Path, str]
            directory to output gacos files
        tar_gz_dir : Optional[Union[Path, str]], optional
            directory to store downloaded *.tar.gz files. If None, then
            `output_dir` is used. Default is None.
        keep_original : bool, optional
            Whether to keep original files (*.tar.gz). Default is False.
        times : Optional[float], optional
            times of acquisition, used to filter out files that are not needed.
            this can be a single time or a list of times. times differ by less
            than 10 minutes are considered the same. Default is None.
        bounds : Optional[tuple[float, float, float, float]], optional
            bounds of area of interest with order (W, S, E, N), used to filter
            out files that are not needed. Default is None.
        """
        self.url_file = Path(url_file)
        self.output_dir = Path(output_dir)
        if tar_gz_dir is None:
            self.tar_gz_dir = self.output_dir
        self.keep_original = keep_original

        if not self.url_file.exists():
            raise FileNotFoundError(f"{self.url_file} does not exist")
        if not self.output_dir.exists():
            self.output_dir.mkdir(parents=True)
        if not self.tar_gz_dir.exists():
            self.tar_gz_dir.mkdir(parents=True)

        self.df_urls = pd.read_csv(self.url_file, header=0)

        # only keep urls that intersect with bounds
        if bounds is not None:
            mask_bbox = self._bbox_mask(BoundingBox(*bounds))

        # only keep urls that acquisition time is within 10 minutes of `time`
        if times is not None:
            if isinstance(times, float):
                times = [times]
            mask_time = self._time_mask(times)

        if bounds is not None and times is not None:
            self.mask = mask_bbox & mask_time
        elif bounds is not None:
            self.mask = mask_bbox
        elif times is not None:
            self.mask = mask_time
        else:
            self.mask = np.ones(self.df_urls.shape[0], dtype=bool)
            
        self.mask = self.mask & self.date_mask

    def _bbox_mask(self, bounds) -> np.ndarray:
        intersection_bbox = np.array(
            [
                BoundingBox(*b).intersects(bounds)
                for b in zip(
                    self.df_urls["south"].astype(float),
                    self.df_urls["west"].astype(float),
                    self.df_urls["north"].astype(float),
                    self.df_urls["east"].astype(float),
                )
            ]
        )
        return intersection_bbox

    def _time_mask(self, times) -> np.ndarray:
        """Only keep urls that acquisition time is within 10 minutes of `time`"""
        intersection_times = []
        for time in times:
            intersection_times.append(
                np.array(
                    np.abs(self.df_urls["time"].astype(float) - time)
                    <= 1 / 60 * 10  # 10 minutes
                )
            )
        intersection_time = np.any(intersection_times, axis=0)
        return intersection_time

    @property
    def date_mask(self) -> np.ndarray:
        """Remove urls that all acquisition dates have been downloaded"""
        dates_urls = self.df_urls["date"].map(lambda x: eval(x))
        intersection_dates = []
        for dt_url in dates_urls:
            intersection_dates.append(~np.all(np.isin(dt_url, self.dates_downloaded)))
        return np.array(intersection_dates)

    @property
    def dates_downloaded(self) -> np.ndarray:
        """Return dates that have been downloaded"""
        gacos_files = list(self.output_dir.rglob("*.ztd.tif"))
        dates = []
        for i in gacos_files:
            stem = i.stem.split(".")[0]
            if len(stem) == 8:
                dates.append(stem)
        return np.array(dates)

    def download(self) -> None:
        """Download GACOS files from URLs in file created by :meth:`GACOSEmail.retrieve_gacos_urls`"""
        urls_used = self.df_urls[self.mask]["url"].values

        for url in tqdm(urls_used, unit="file", desc="Downloading GACOS files"):
            gz_file = self.tar_gz_dir / Path(url).name
            downloader.download_data(url, file_name=gz_file)
            self._extract_tar_gz(gz_file)
            if not self.keep_original:
                self._delete_file(gz_file)

    def _extract_tar_gz(self, gz_file) -> None:
        """Unzip/extract downloaded GACOS files

        Parameters
        ----------
        gz_file : Path
            path to downloaded GACOS file (*.tar.gz)
        """
        with tarfile.open(gz_file, "r:gz") as tar:
            tar.extractall(path=self.output_dir)

    def _delete_file(self, gz_file) -> None:
        """Delete original GACOS files

        Parameters
        ----------
        gz_file : Path
            path to downloaded GACOS file (*.tar.gz)
        """
        gz_file.unlink()
