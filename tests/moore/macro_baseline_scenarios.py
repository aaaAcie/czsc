# -*- coding: utf-8 -*-
"""macro stability 回归场景定义。"""

MACRO_SCENARIOS = [
    {
        "name": "600707_macro_sync_stability",
        "symbol": "600707",
        "sdt": "20140601",
        "edt": "20210820",
        "kwargs": {
            "ma34_cross_as_valid_gate": True,
            "audit_link_rounds": 3,
            "enable_pre_round": True,
            "replay_centers_after_macro_swallow": False,
        },
    },
    {
        "name": "300371_macro_sensitive",
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
        "name": "300490_delayed_chain_heavy",
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
]
