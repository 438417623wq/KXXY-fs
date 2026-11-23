# -*- coding: utf-8 -*-
"""硬件写入闸门、可恢复会话和有界写前日志。"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, List, Optional

from hardware.ec import EcBus, EcTimeout


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


class Journal:
    MAX_LINES = 8000
    KEEP_LINES = 3000

    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._lock = threading.RLock()
        self.entries = []
        self._index = {}
        self._lines = 0
        self._load()

    def _load(self):
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as fh:
            for line in fh:
                self._lines += 1
                try:
                    rec = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(rec, dict) or not rec.get("id"):
                    continue
                rid = rec["id"]
                if rid in self._index:
                    self._index[rid].update(rec)
                elif "kind" in rec:
                    self.entries.append(rec)
                    self._index[rid] = rec
        self._compact()

    def _compact(self, force=False):
        if not force and len(self.entries) <= self.MAX_LINES and self._lines <= self.MAX_LINES * 3:
            return
        # 每个未恢复目标保留恢复锚点。寄存器保留最初 old，风扇保留最新意图。
        anchors = {}
        for rec in self.entries:
            if rec.get("reverted"):
                continue
            key = (rec.get("kind"), rec.get("target"))
            if key[0] == "ec_ram":
                anchors.setdefault(key, rec)
            else:
                anchors[key] = rec
        keep = {r["id"]: r for r in anchors.values()}
        remaining = max(0, self.KEEP_LINES - len(keep))
        for rec in reversed(self.entries):
            if remaining <= 0:
                break
            if rec["id"] not in keep:
                keep[rec["id"]] = rec
                remaining -= 1
        selected = [r for r in self.entries if r["id"] in keep]
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            for rec in selected:
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.path)
        self.entries = selected
        self._index = {r["id"]: r for r in selected}
        self._lines = len(selected)

    def _append(self, rec):
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            fh.flush()
            # 意图必须在硬件写入前持久化；状态补丁下次恢复仍以不确定态处理。
            if rec.get("status") == "pending":
                os.fsync(fh.fileno())
        self._lines += 1

    def begin(self, kind, target, old, new, note=""):
        rid = uuid.uuid4().hex[:12]
        rec = dict(id=rid, ts=_now(), kind=kind, target=target, old=old, new=new,
                   note=note, status="pending", reverted=False)
        with self._lock:
            self._append(rec)
            self.entries.append(rec)
            self._index[rid] = rec
            self._compact()
        return rid

    def finish(self, rid, status="ok", note=""):
        with self._lock:
            rec = self._index.get(rid)
            if rec is None:
                return
            patch = dict(id=rid, status=status, note=note or rec.get("note", ""))
            self._append(patch)
            rec.update(patch)
            self._compact()

    def mark_reverted(self, rid):
        self.mark_reverted_many([rid])

    def mark_reverted_many(self, rids):
        with self._lock:
            for rid in rids:
                rec = self._index.get(rid)
                if rec and not rec.get("reverted"):
                    self._append(dict(id=rid, reverted=True))
                    rec["reverted"] = True
            self._compact()

    def pending(self):
        with self._lock:
            return [dict(r) for r in self.entries if not r.get("reverted") and r.get("status") in ("pending", "fail")]

    def recent(self, limit=60):
        with self._lock:
            return [dict(r) for r in reversed(self.entries[-limit:])]

    def clear_history(self):
        # 清历史不能抹掉未恢复操作；仍保留各目标的恢复锚点。
        with self._lock:
            old = self.KEEP_LINES
            try:
                self.KEEP_LINES = 0
                self._compact(force=True)
            finally:
                self.KEEP_LINES = old


class SessionGuard:
    def __init__(self, data_dir, config):
        self.data_dir, self.config = data_dir, config
        os.makedirs(data_dir, exist_ok=True)
        self.journal = Journal(os.path.join(data_dir, "journal.jsonl"))
        self.session_file = os.path.join(data_dir, "session.json")
        self.bus = None
        self.lock = threading.RLock()
        self._shutdown_event = threading.Event()
        self._suspend_event = threading.Event()
        self.fan_mode, self.fan_last_duty, self.ram_touched = {}, {}, {}
        self.unclean = False
        self.session_started = _now()
        self._load_session()

    @property
    def writes_blocked(self):
        return self._shutdown_event.is_set() or self._suspend_event.is_set()

    def begin_shutdown(self):
        # 不拿硬件锁，立即封禁已排队但尚未执行的非恢复写入。
        self._shutdown_event.set()

    def begin_suspend(self):
        self._suspend_event.set()

    def end_suspend(self):
        self._suspend_event.clear()

    def bind(self, bus):
        self.bus = bus

    def _load_session(self):
        try:
            with open(self.session_file, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if not data.get("clean", False):
                self.unclean = True
                self.ram_touched = {int(k): int(v) for k, v in data.get("ram_touched", {}).items()}
                self.fan_mode = {int(k): v for k, v in data.get("fan_mode", {}).items()}
        except FileNotFoundError:
            pass
        except (ValueError, TypeError, AttributeError, OSError):
            self.unclean = True
        # 即使会话文件丢失，WAL 仍可找回最初寄存器值和不确定风扇写入。
        for rec in self.journal.entries:
            if rec.get("reverted"):
                continue
            try:
                if rec.get("kind") == "ec_ram":
                    self.ram_touched.setdefault(int(rec["target"], 16), int(rec["old"]))
                    self.unclean = True
                elif rec.get("kind") in ("fan_duty", "fan_auto"):
                    no = int(rec["target"][3:])
                    self.fan_mode.setdefault(no, "unknown")
                    self.unclean = True
            except (ValueError, TypeError, KeyError):
                self.unclean = True

    def _has_unrestored(self):
        return bool(self.ram_touched) or any(v != "auto" for v in self.fan_mode.values())

    def _save_session(self, clean=False):
        clean = bool(clean and not self._has_unrestored())
        payload = dict(started=self.session_started, clean=clean,
                       ram_touched={str(k): v for k, v in self.ram_touched.items()},
                       fan_mode={str(k): v for k, v in self.fan_mode.items()})
        tmp = self.session_file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, self.session_file)
        self.unclean = not clean

    def mark_running(self):
        with self.lock:
            self._save_session(False)

    def close_clean(self):
        with self.lock:
            safe = not self._has_unrestored() and all(f.get("mode", "auto") == "auto" for f in self.config["fans"].values())
            self._save_session(safe)
            return safe

    def fan_duty(self, fan_no, percent, note=""):
        if not self.bus or self.writes_blocked:
            return dict(ok=False, reason="硬件未就绪或写入已封禁")
        with self.lock:
            if self.writes_blocked:
                return dict(ok=False, reason="控制器正在退出或休眠")
            cfg = self.config["safety"]
            pct = int(max(0, min(100, percent)))
            clamped = pct < cfg["min_duty"] and not cfg["allow_low_duty"]
            if clamped:
                pct = cfg["min_duty"]
            rid = self.journal.begin("fan_duty", f"fan{fan_no}", self.fan_mode.get(fan_no, "auto"), pct, note)
            self.fan_mode[fan_no] = "unknown"
            self._save_session(False)
            try:
                if self.writes_blocked:
                    self.journal.finish(rid, "fail", "写入前收到退出或休眠")
                    return dict(ok=False, reason="写入已封禁")
                self.bus.set_fan_duty(fan_no, pct)
            except Exception as exc:
                self.journal.finish(rid, "fail", str(exc))
                return dict(ok=False, reason=str(exc))
            self.fan_mode[fan_no] = "manual"
            self.fan_last_duty[fan_no] = pct
            self.journal.finish(rid)
            self._save_session(False)
            return dict(ok=True, duty=pct, clamped=clamped, id=rid)

    def _revert_target(self, target):
        with self.journal._lock:
            rids = [r["id"] for r in self.journal.entries if r.get("target") == target and not r.get("reverted")]
            self.journal.mark_reverted_many(rids)

    def fan_auto(self, fan_no, note=""):
        if not self.bus:
            return dict(ok=False, reason="EC 未就绪")
        with self.lock:
            rid = self.journal.begin("fan_auto", f"fan{fan_no}", self.fan_mode.get(fan_no, "unknown"), "auto", note)
            self.fan_mode[fan_no] = "unknown"
            self._save_session(False)
            try:
                self.bus.set_fan_auto(fan_no)
            except Exception as exc:
                self.journal.finish(rid, "fail", str(exc))
                return dict(ok=False, reason=str(exc))
            self.fan_mode[fan_no] = "auto"
            self.fan_last_duty.pop(fan_no, None)
            self.journal.finish(rid)
            self._revert_target(f"fan{fan_no}")
            self._save_session(False)
            return dict(ok=True, id=rid)

    def ec_write(self, addr, value, note=""):
        if not self.bus or self.writes_blocked:
            return dict(ok=False, reason="EC 未就绪或写入已封禁")
        if type(addr) is not int or not 0 <= addr <= 255 or type(value) is not int or not 0 <= value <= 255:
            return dict(ok=False, reason="寄存器地址和值必须是字节")
        with self.lock:
            if self.writes_blocked:
                return dict(ok=False, reason="控制器正在退出或休眠")
            try:
                old = self.bus.read_ram(addr)
                self.ram_touched.setdefault(addr, old)
                rid = self.journal.begin("ec_ram", f"0x{addr:02X}", self.ram_touched[addr], value, note)
                self._save_session(False)
                if self.writes_blocked:
                    self.journal.finish(rid, "fail", "写入前收到退出或休眠")
                    return dict(ok=False, reason="写入已封禁")
                self.bus.write_ram(addr, value)
            except Exception as exc:
                if "rid" in locals():
                    self.journal.finish(rid, "fail", str(exc))
                return dict(ok=False, reason=str(exc))
            self.journal.finish(rid)
            return dict(ok=True, old=old, id=rid)

    def restore_ram(self, addr):
        with self.lock:
            if not self.bus or addr not in self.ram_touched:
                return dict(ok=False, reason="没有原始记录")
            try:
                self.bus.write_ram(addr, self.ram_touched[addr])
                self._revert_target(f"0x{addr:02X}")
                del self.ram_touched[addr]
                self._save_session(False)
                return dict(ok=True)
            except Exception as exc:
                return dict(ok=False, reason=str(exc))

    def restore_all(self, fans=None):
        if not self.bus:
            return dict(ok=False, done=[], errors=["EC 未就绪"])
        done, errors = [], []
        with self.lock:
            targets = fans if fans is not None else sorted(set(self.fan_mode) | {int(k) for k in self.config["fans"]})
            for no in targets:
                try:
                    res = self.fan_auto(no, "还原风扇")
                    if res.get("ok"):
                        done.append(f"风扇{no} → 自动")
                    else:
                        errors.append(f"风扇{no}：{res.get('reason', '')}")
                except Exception as exc:
                    errors.append(f"风扇{no}：{exc}")
            for addr in list(self.ram_touched):
                res = self.restore_ram(addr)
                if res.get("ok"):
                    done.append(f"0x{addr:02X} → 原值")
                else:
                    errors.append(f"0x{addr:02X}：{res.get('reason', '')}")
            # 还原硬件与提交退出配置是两步；仅 Runner 成功保存自动策略后才可 close_clean。
            self._save_session(clean=False)
        return dict(ok=not errors, done=done, errors=errors)

    def status(self):
        # UI 不能等住阻塞的硬件锁；这些副本可能相差一拍，不作为控制依据。
        return dict(unclean=self.unclean, session_started=self.session_started,
                    fan_mode={str(k): v for k, v in list(self.fan_mode.items())},
                    ram_touched={f"0x{k:02X}": v for k, v in list(self.ram_touched.items())},
                    pending=len(self.journal.pending()), entries=self.journal.recent(40))
