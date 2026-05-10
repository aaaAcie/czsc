# -*- coding: utf-8 -*-
"""micro_engine helper 重构回归基线场景定义。"""

SCENARIOS = [
    {
        "name": "300490_core_delayed_chain",
        "symbol": "300490",
        "sdt": "20160115",
        "edt": "20210701",
        "kwargs": {
            "ma34_cross_as_valid_gate": True,
            "audit_link_rounds": 3,
            "enable_pre_round": True,
            "replay_centers_after_macro_swallow": False,
        },
    },
    {
        "name": "300371_macro_swallow_regression",
        "symbol": "300371",
        "sdt": "20181220",
        "edt": "20201030",
        "kwargs": {
            "ma34_cross_as_valid_gate": True,
            "audit_link_rounds": 3,
            "replay_centers_after_macro_swallow": False,
        },
    },
    {
        "name": "300339_center_5k_leavek",
        "symbol": "300339",
        "sdt": "20181201",
        "edt": "20190430",
        "kwargs": {
            "ma34_cross_as_valid_gate": True,
            "audit_link_rounds": 3,
            "replay_centers_after_macro_swallow": False,
        },
    },
    {
        "name": "002346_min_expand",
        "symbol": "002346",
        "sdt": "20181201",
        "edt": "20201030",
        "kwargs": {
            "ma34_cross_as_valid_gate": True,
            "audit_link_rounds": 3,
            "replay_centers_after_macro_swallow": False,
        },
    },
    {
        "name": "300137_min_expand",
        "symbol": "300137",
        "sdt": "20181201",
        "edt": "20201030",
        "kwargs": {
            "ma34_cross_as_valid_gate": True,
            "audit_link_rounds": 3,
            "replay_centers_after_macro_swallow": False,
        },
    },
]
