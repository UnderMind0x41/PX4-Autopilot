#!/usr/bin/env bash
# This branch deliberately keeps the v1.17.0 release base.
printf '%s\n' 'custom-v1.17.0: upstream/main is a development branch. Selectively port fixes instead of merging it.' >&2
exit 1
