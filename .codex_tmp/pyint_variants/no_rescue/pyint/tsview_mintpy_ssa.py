#!/usr/bin/env python
# Load the usual suspects:
import os
import sys
import argparse
import pandas as pd
import numpy as np
import math
import matplotlib.pyplot as plt
from mintpy.utils import utils as ut
from ssa import SSA
from sklearn.metrics import mean_squared_error


def create_parser():
    parser = argparse.ArgumentParser(description='SSA time series analysis for a specific location')
    parser.add_argument('ts_dir', help='Time series directory (e.g., ./SBAS_atm_gacos)')
    parser.add_argument('ts_file', help='Time series file name (e.g., geo/geo_timeseries_SET_GACOS_ramp_demErr.h5)')
    parser.add_argument('lat', type=float, help='Latitude of the point')
    parser.add_argument('lon', type=float, help='Longitude of the point')
    parser.add_argument('-L', '--window', type=int, default=8, help='Window length for SSA (default: 8)')
    parser.add_argument('-o', '--output', default='ssa_ts.txt', help='Output file name (default: ssa_ts.txt)')
    return parser


def parse_args():
    parser = create_parser()
    return parser.parse_args()


def DecYr(x):
    return x.dt.to_period('D').dt.to_timestamp().dt.year + x.dt.to_period('D').dt.to_timestamp().dt.dayofyear / 365.25


def sigma_sum(Sigma):
    sigma_sumsq = (Sigma**2).sum()
    return Sigma**2 / sigma_sumsq * 100


def main():
    args = parse_args()

    proj_dir = os.path.expanduser(args.ts_dir)
    ts_file = os.path.join(proj_dir, args.ts_file)
    geom_file = None

    print(f"Reading time series from: {ts_file}")
    print(f"Location: lat={args.lat}, lon={args.lon}")
    print(f"Window length: {args.window}")

    dates, dis, std = ut.read_timeseries_lalo(lat=args.lat, lon=args.lon, ts_file=ts_file, lookup_file=geom_file)

    # Convert from meter to mm and save to panda df
    df = pd.DataFrame({'date': dates, 'dis': dis*1000})

    # Decomposition
    F_ssa = SSA(dis*1000, args.window)
    contri = sigma_sum(F_ssa.Sigma)
    df_ssa = pd.concat([F_ssa.components_to_df()], axis=1)
    np.savetxt('sigma_ssa.txt', np.c_[np.arange(1, args.window+1), contri], fmt="%.2f")
    df_comp = pd.concat([df['date'], F_ssa.components_to_df()], axis=1)
    print('\n \n', 'Writing SSA all components  to: ssa_all_compo.txt ', '\n')
    df_comp.to_csv('ssa_all_compo.txt', index=False, header=True, sep='\t')

    plt.rcParams.update({'font.size': 10})
    fig, ax = plt.subplots(nrows=args.window, ncols=1, sharex=True, figsize=(5, 11*args.window))
    for i, column in enumerate(df_ssa.columns):
        ax[i].plot(df_ssa.index, df_ssa[column], label=column)
        ax[i].set_title(f'{column} ({contri[i]:.1f}%)')
        plt.subplots_adjust(hspace=.5)

    # Reconstruction
    trend_indices = list(range(min(2, args.window)))
    noise_indices = list(range(2, args.window))

    df['ssa_trend'] = pd.DataFrame(F_ssa.reconstruct(trend_indices))
    df['noise'] = pd.DataFrame(F_ssa.reconstruct(noise_indices)) if noise_indices else 0
    print('\n \n', 'Writing to:  ', args.output, '\n')
    df.to_csv(args.output, index=False, header=True, sep='\t')

    fig, ax = plt.subplots(nrows=3, ncols=1, sharex=True, figsize=(10, 10))

    ax[0].plot(df['date'], df['dis'], label='InSAR')
    ax[0].set_title('InSAR')
    ax[1].plot(df['date'], df['noise'], label='Noise')
    ax[1].set_title('Periodic+Noise')
    ax[2].plot(df['date'], df['ssa_trend'], label='Trend')
    ax[2].set_title('Trend')
    plt.show()


if __name__ == '__main__':
    main()
