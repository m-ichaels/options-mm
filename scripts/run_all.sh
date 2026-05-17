#!/usr/bin/env bash
# Full pipeline: raw data -> tape tables -> surfaces on the tape, from the trade history and from the listed chains ->
# the quoters on the tape and the sensitivities -> microstructure -> RFQ examples -> tests -> figures -> summary ->
# report -> notebook.   Usage: scripts/run_all.sh [--skip-download] [--quick]
# The recorder (tools/record.py) is separate: start it in its own terminal and leave it running; the pipeline uses
# whatever tape it has produced.  OPTMM_DATA=data/sample runs everything on the small committed sample.
set -euo pipefail
Q=""
[[ " $* " == *" --quick "* ]] && Q="--quick"
if [[ " $* " != *" --skip-download "* ]]; then
  python tools/download.py instruments
  python tools/download.py trades --from 2021-01-01
  python tools/download.py dvol
  python tools/download.py chains
  python tools/download.py prices
  python tools/download.py rates
fi
python build_ext.py build_ext --inplace || echo "C++ extension not built; the numpy solver is used"
python -m optmm tape
python -m optmm run $Q | tee results/run.log
python -m pytest -q | tee results/tests.txt
python scripts/plots.py
python scripts/summarize.py
python scripts/report.py
python scripts/make_notebook.py --execute
echo done
