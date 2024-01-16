#!/usr/bin/env python3

"""Plot the output of `borg list`

This script makes a simple plot of the archive timestamps listed by
the `borg list` command, giving a convenient visual overview of
the archives in a borg repository.
"""

import sys
from datetime import datetime

import pandas as pd
import matplotlib.pyplot as plt
from pandas.plotting import register_matplotlib_converters
register_matplotlib_converters()

"""Plot archive datestamps from `borg list` output"""

def main():
    df = pd.read_fwf(
        sys.argv[1],
        widths=[37, 24],
        header=None,
        parse_dates=[1]
    )
    plt.figure(figsize=(3, 12))
    plt.yticks(ticks=df[1], labels=make_labels(df[1]))
    plt.xticks(ticks=[], labels=[])
    plt.hlines(df[1], 0, 1)
    plt.subplots_adjust(left=0.7, right=0.9, top=0.95, bottom=0.05)
    plt.show()


def make_labels(dates: list) -> list:
    labels = [pd.to_datetime(t).strftime('%Y-%m-%d') for t in dates]
    limit = pd.Timedelta("36 hours")
    for i in range(2, len(dates)-1):
        if (dates[i] - dates[i - 1] < limit
            and dates[i + 1] - dates[i] < limit):
            labels[i] = ''
    return labels


if __name__ == "__main__":
    main()
