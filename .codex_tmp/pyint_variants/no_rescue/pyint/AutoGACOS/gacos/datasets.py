import warnings
from pathlib import Path
from typing import Literal, Optional, Union

import numpy as np
import pandas as pd
try:
    from faninsar.datasets import HyP3, LiCSAR
except ImportError:
    try:
        from faninsar.datasets import hyp3 as HyP3, licsar as LiCSAR
    except ImportError:
        HyP3 = None
        LiCSAR = None

warnings.filterwarnings("ignore")


class SarDataset:
    def __init__(
        self,
        bounds: tuple[float, float, float, float],
        date_times: pd.DatetimeIndex,
        gacos_dir: Optional[Union[Path, str]] = None,
    ) -> None:
        """Initialize SarDataset class

        Parameters
        ----------
        bounds : tuple[float, float, float, float]
            The bounding box of the dataset.
        date_times : pd.DatetimeIndex
            The datetime index of the dataset.
        gacos_dir : Optional[Union[Path, str]], optional
            The directory used to save gacos data. Used to check if the data is
            already downloaded and avoid resubmitting. Default is None.
        """
        self.bounds = bounds
        self._date_times = date_times

        self._dates = date_times.strftime("%Y%m%d")

        if gacos_dir is not None:
            self._dates_remain = self._get_dates_remain(gacos_dir)
        else:
            self._dates_remain = self.dates

        hour = date_times.hour
        minute = np.round((date_times.second / 60) + date_times.minute).astype(int)
        times = pd.Series([f"{h:02d}:{m:02d}" for h, m in zip(hour, minute)])
        self._times = times

        self._times_remain = self._get_times_remain()

    def __str__(self) -> str:
        return (
            f"{self.__class__.__name__}(\n"
            f"    bounds={self.bounds}, \n"
            f"    times={len(self.times)}, \n"
            f"    dates={len(self.dates)}\n"
            ")"
        )

    def __repr__(self) -> str:
        return self.__str__()

    def _get_dates_remain(self, gacos_dir: Union[Path, str]):
        """Get the dates that are not downloaded yet.
        Parameters
        ----------
        gacos_dir : Union[Path, str]
            The directory used to save gacos data. Used to check if the data is
            already downloaded and avoid resubmitting.

        Returns
        -------
        dates_remain : np.ndarray
            The dates that are not downloaded yet.
        """
        gacos_files = list(Path(gacos_dir).rglob("*.ztd.tif"))
        gacos_dates = []
        for i in gacos_files:
            stem = i.stem.split(".")[0]
            if len(stem) == 8:
                gacos_dates.append(stem)
        dates_remain = np.setdiff1d(self.dates, gacos_dates)
        return dates_remain

    def _get_times_remain(self):
        """Get the times corresponding to the dates that are not downloaded yet."""
        idx = np.where(np.isin(self.dates, self.dates_remain))[0]
        times_remain = self._times[idx]
        return times_remain

    @property
    def dates(self):
        """The dates (YYYYMMDD) of the acquisitions parsed from dataset."""
        return self._dates

    @property
    def times(self):
        """The times (HH:MM) of the acquisitions parsed from dataset."""
        return self._times.unique()

    @property
    def date_times(self):
        """The datetime of the acquisitions parsed from dataset."""
        return self._date_times

    @property
    def dates_remain(self):
        """The dates that are not downloaded yet. If gacos_dir is None, then
        dates_remain is the same as dates."""
        return self._dates_remain

    @property
    def times_remain(self):
        """The times corresponding to the dates that are not downloaded yet."""
        return self._times_remain

    def gen_datetime_patches(
        self,
        mode: Literal["all", "remain"] = "remain",
    ) -> dict:
        """Generate datetime patches.

        Parameters
        ----------
        mode : Literal["all", "remain"], optional
            The mode to generate datetime patches. If "all", then generate all
            the datetime patches. If "remain", then generate the datetime
            patches of the dates that are not downloaded yet. Default is
            "remain".

        Returns
        -------
        datetime_patches : dict
            The datetime patches. The key is the time (HH:MM) and the value is
            the datetime patches.
        """
        nums = 20
        datetime_patches = {}

        if mode == "all":
            for _time in self.times:
                _dts = self.dates[self._times == _time]
                n_patch = np.ceil(len(_dts) / nums)
                dates_patch = np.array_split(_dts, n_patch)
                datetime_patches[_time] = dates_patch
        elif mode == "remain":
            for _time in self.times_remain:
                _dts = self.dates_remain[self._times_remain == _time]
                n_patch = np.ceil(len(_dts) / nums)
                dates_patch = np.array_split(_dts, n_patch)
                datetime_patches[_time] = dates_patch

        return datetime_patches

    def gen_post_data(
        self,
        dates: Union[list, np.ndarray],
        times: Union[tuple[int, int], tuple[str, str]],
        email: str,
    ):
        """Generate post data for gacos website.

        Parameters
        ----------
        dates : list or np.ndarray
            The list of dates.
        times : tuple[int, int]
            The time of the acquisition (hour, minute).
        email : str
            The email address to receive the gacos data.

        Returns
        -------
        post_data : dict
            The post data.
        """
        if isinstance(dates, np.ndarray):
            dates = dates.tolist()
        times = [int(t) for t in times]

        post_data = {
            "N": self.bounds[3],
            "W": self.bounds[0],
            "S": self.bounds[1],
            "E": self.bounds[2],
            "H": times[0],
            "M": times[1],
            "date": "\n".join(dates),
            "type": "2",
            "email": email,
        }
        return post_data


class LiCSARDataset(SarDataset):
    def __init__(
        self,
        home_dir: Union[Path, str],
        gacos_dir: Optional[Union[Path, str]] = None,
    ) -> None:
        """Initialize LiCSARDataset class

        Parameters
        ----------
        home_dir : Union[Path, str]
            The home directory of LiCSAR dataset.
        gacos_dir : Optional[Union[Path, str]], optional
            The directory used to save gacos data. Used to check if the data is
            already downloaded and avoid resubmitting. Default is None.
        """
        self.home_dir = Path(home_dir)
        self.dataset = LiCSAR(home_dir)
        bounds = self.dataset.bounds
        time = self._get_time()
        dates = self.dataset.pairs.dates
        date_times = pd.to_datetime([f"{d} {time[0]}:{time[1]}:00" for d in dates])
        super().__init__(bounds, date_times, gacos_dir)

    def _get_time(self):
        """Get the acquisition time of acquisitions.

        Returns
        -------
        time: tuple[int, int]
            A tuple of hour and minute representing the acquisition time.

        Raises
        ------
        ValueError
            If no center_time found in metadata.txt.
        """
        meta_file = sorted(self.home_dir.rglob("metadata.txt"))[0]

        with open(meta_file) as f:
            lines = f.readlines()
            time = None
            for line in lines:
                line_split = line.split("=")
                key, value = (line_split[0].strip(), line_split[1])
                if "center_time" == key:
                    center_time = value.strip()
                    hour, minute, second = center_time.split(":")
                    hour, minute, second = int(hour), int(minute), float(second)
                    minute = minute + int(np.round(second / 60, 0))
                    return hour, minute
                else:
                    continue
            if time is None:
                raise ValueError(f"No center_time found in {meta_file}")


class HyP3Dataset(SarDataset):
    def __init__(
        self,
        home_dir: Union[Path, str],
        gacos_dir: Optional[Union[Path, str]] = None,
    ) -> None:
        """Initialize HyP3Dataset class

        Parameters
        ----------
        home_dir : Union[Path, str]
            The home directory of HyP3 dataset.
        gacos_dir : Optional[Union[Path, str]], optional
            The directory used to save gacos data. Used to check if the data is
            already downloaded and avoid resubmitting. Default is None.
        """
        self.dataset = HyP3(home_dir)
        bounds = self.dataset.bounds.to_crs("epsg:4326")
        date_times = self.dataset.datetime

        super().__init__(bounds, date_times, gacos_dir)
