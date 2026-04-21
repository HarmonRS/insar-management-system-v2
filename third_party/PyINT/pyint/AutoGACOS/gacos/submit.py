import time
from pathlib import Path
from typing import Optional, Union

import numpy as np
import requests
from tqdm.auto import tqdm

from .datasets import SarDataset


class Submitter:
    def __init__(
        self,
        dataset: SarDataset,
        email: str,
        sleep_time_range: tuple[int, int] = (60 / 2, 60 * 5),
        gacos_url="http://www.gacos.net/M/action_page.php",
    ) -> None:
        """Initialize Submitter class

        Parameters
        ----------
        dataset : SarDataset
            The SarDataset object.
        email : str
            The email address to submit to gacos.
        sleep_time_range : tuple[int, int], optional
            The range of sleep time in seconds. Default is (60, 60 * 5).
        gacos_url : str, optional
            The url of gacos website. Default is "http://www.gacos.net/M/action_page.php".
        """
        self.dataset = dataset
        self.email = email
        self.sleep_time_range = sleep_time_range
        self.gacos_url = gacos_url

        self._failed = []
        self._succeed = []

    def _post_data(self, data):
        """Post data to gacos website."""
        r = requests.post(self.gacos_url, data=data)
        return "Thanks for using GACOS!" in r.text

    def post_requests(self):
        # post gacos info to website
        datetime_patches = self.dataset.gen_datetime_patches()
        for _key, _dates in tqdm(
            datetime_patches.items(),
            desc="submitting times",
            unit="times",
        ):
            try:
                for _dt in tqdm(_dates, desc="submitting dates", unit="dates"):
                    post_data = self.dataset.gen_post_data(
                        _dt, _key.split(":"), self.email
                    )
                    status_ok = self._post_data(post_data)
                    if status_ok:
                        self._succeed.append(post_data)
                        tqdm.write(f">>> succeed post: {post_data}")
                    else:
                        self._failed.append(post_data)
                        tqdm.write(f">>> failed post: {post_data}")

                    # wait to avoid be rejected
                    sleep_time = np.random.randint(*self.sleep_time_range)
                    tqdm.write(f"    sleeping for {sleep_time} seconds...")
                    time.sleep(sleep_time)
            except:
                self._failed.append(post_data)
                tqdm.write(f">>> failed post: {post_data}")

    @property
    def failed(self):
        """A list of failed post data."""
        return self._failed

    @property
    def succeed(self):
        """A list of succeed post data."""
        return self._succeed
