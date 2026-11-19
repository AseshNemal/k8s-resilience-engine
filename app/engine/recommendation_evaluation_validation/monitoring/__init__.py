"""Measured behaviour of an application while a worker node is failing, sampled over time.

Everything here is MEASURED from the cluster and from real requests, never computed by the failure model: the recorder keeps
what each reader returned and the summary only counts and orders it. A reader that could not answer leaves that value unknown
(``None``), and an unknown value is a gap, never a zero. See ``timeline.py`` (the record), ``summary.py`` (what the record
shows), ``sampler.py`` (the clock that fills it) and ``readers.py`` (where the values come from).
"""
