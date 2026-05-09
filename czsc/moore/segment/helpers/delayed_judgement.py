# -*- coding: utf-8 -*-
"""延迟结算链 helper：维护待定队列与节点结算。"""

from ...objects import PendingJudgementNode


class DelayedJudgementHelper:
    def __init__(self, state, has_center_between, reset_locks, update_segments):
        self.s = state
        self._has_center_between = has_center_between
        self._reset_locks = reset_locks
        self._update_segments = update_segments

    def _find_tk_in_turnings(self, micro_id: int):
        for i, tk in enumerate(self.s.turning_ks):
            if tk.cache.get("micro_id") == micro_id:
                return i, tk
        return -1, None

    def _get_tk_by_id(self, micro_id: int):
        _, tk = self._find_tk_in_turnings(micro_id)
        if tk is not None:
            return tk
        return self.s.turning_tk_store.get(micro_id)

    def _find_node_by_candidate(self, candidate_id: int):
        s = self.s
        for node_id in reversed(list(s.pending_judgements)):
            node = s.judgement_nodes.get(node_id)
            if not node or node.stage in ("resolved", "cancelled"):
                continue
            if node.candidate_id == candidate_id:
                return node
        return None

    def enqueue_or_advance(self, refreshed_old_tk, final_tk):
        s = self.s
        final_id = final_tk.cache.get("micro_id")
        if final_id is None:
            return

        if refreshed_old_tk is not None:
            base_id = refreshed_old_tk.cache.get("micro_id")
            if base_id is None:
                return
            s.judgement_id_seed += 1
            node_id = s.judgement_id_seed
            parent = self._find_node_by_candidate(base_id)
            parent_id = parent.id if parent else None
            node = PendingJudgementNode(
                id=node_id,
                base_id=base_id,
                candidate_id=final_id,
                created_k_idx=final_tk.k_index,
                created_dt=final_tk.dt,
                parent_id=parent_id,
            )
            if parent:
                parent.child_ids.append(node_id)
            s.judgement_nodes[node_id] = node
            s.pending_judgements.append(node_id)
            s.debug_judgement_events.append({
                "event": "enqueue",
                "node_id": node_id,
                "base_id": base_id,
                "candidate_id": final_id,
                "dt": final_tk.dt,
            })
            return

        for node_id in list(s.pending_judgements):
            node = s.judgement_nodes.get(node_id)
            if not node or node.stage in ("resolved", "cancelled"):
                continue

            _, candidate_tk = self._find_tk_in_turnings(node.candidate_id)
            if candidate_tk is None:
                node.stage = "cancelled"
                continue

            if node.stage == "wait_anchor_start":
                if final_tk.mark != candidate_tk.mark:
                    node.stage = "wait_anchor_real"
                    node.c_candidate_id = final_id
                    s.debug_judgement_events.append({
                        "event": "anchor_start",
                        "node_id": node_id,
                        "c_candidate_id": final_id,
                        "dt": final_tk.dt,
                    })
                continue

            if node.stage != "wait_anchor_real" or node.c_candidate_id is None:
                continue

            _, c_tk = self._find_tk_in_turnings(node.c_candidate_id)
            if c_tk is None:
                node.stage = "cancelled"
                continue

            if final_tk.mark == c_tk.mark:
                node.c_candidate_id = final_id
                continue

            node.resolve_anchor_id = node.c_candidate_id
            node.stage = "ready_resolve"
            s.debug_judgement_events.append({
                "event": "anchor_real",
                "node_id": node_id,
                "resolve_anchor_id": node.resolve_anchor_id,
                "dt": final_tk.dt,
            })

    def resolve_ready(self):
        s = self.s
        changed = False
        for node_id in list(s.pending_judgements):
            node = s.judgement_nodes.get(node_id)
            if not node or node.stage != "ready_resolve" or node.resolve_anchor_id is None:
                continue

            if s.last_resolve_anchor_id == node.resolve_anchor_id:
                node.stage = "resolved"
                continue

            base_tk = self._get_tk_by_id(node.base_id)
            cand_tk = self._get_tk_by_id(node.candidate_id)
            anchor_tk = self._get_tk_by_id(node.resolve_anchor_id)
            if base_tk is None or cand_tk is None or anchor_tk is None:
                node.stage = "cancelled"
                continue

            cand_ok = self._has_center_between(cand_tk.k_index, anchor_tk.k_index)
            base_ok = self._has_center_between(base_tk.k_index, anchor_tk.k_index)

            if (not cand_ok) and base_ok:
                node.resolution = "rollback_base"
                self._apply_rollback_to_base(node)
                changed = True
            else:
                node.resolution = "keep_candidate"

            node.stage = "resolved"
            node.resolved_k_idx = anchor_tk.k_index
            node.resolved_dt = anchor_tk.dt
            s.last_resolve_anchor_id = node.resolve_anchor_id
            s.debug_judgement_events.append({
                "event": "resolved",
                "node_id": node.id,
                "resolution": node.resolution,
                "resolve_anchor_id": node.resolve_anchor_id,
                "dt": anchor_tk.dt,
            })

        s.pending_judgements = type(s.pending_judgements)(
            [nid for nid in s.pending_judgements if s.judgement_nodes.get(nid) and s.judgement_nodes[nid].stage not in ("resolved", "cancelled")]
        )
        if changed:
            self._reset_locks()
            self._update_segments()

    def _apply_rollback_to_base(self, node: PendingJudgementNode):
        cand_idx, _ = self._find_tk_in_turnings(node.candidate_id)
        base_tk = self._get_tk_by_id(node.base_id)
        if cand_idx < 0 or base_tk is None:
            return
        self.s.turning_ks[cand_idx] = base_tk
