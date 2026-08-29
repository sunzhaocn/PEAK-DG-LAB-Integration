"""Coyote visual rule graph.

User rules are JSON node graphs instead of .py scripts. Ordinary rules stay
blocked while PEAK reports dead/passedOut. Only dedicated death/passed-out graph
triggers may use the privileged in-game incapacitation output path.
"""
from __future__ import annotations

import json, math, random, threading, time, uuid
import backend as B

VERSION = 1
GRAPH_FILE = B.ROOT / "visual_rules.json"
DOC_FILE = B.DOC_DIR / "图形化规则使用说明.md"
_SPECIAL_TYPES = {"death", "passed"}
_SPECIAL_KEYS = {"dead", "passedOut"}
_LOCK = threading.RLock()
_RUNTIME_LOCK = threading.RLock()
_EVENTS = threading.local()
_BACKEND_INSTALLED = False
_UI_INSTALLED = False
graphs = []
valid_graph_ids = set()
runtime = {}


def _id(prefix="n"): return f"{prefix}_{uuid.uuid4().hex[:10]}"
def _copy(v): return json.loads(json.dumps(v, ensure_ascii=False))
def _num(v, d=0.0):
    try:
        v=float(v); return v if math.isfinite(v) else float(d)
    except Exception: return float(d)
def _truth(v):
    if isinstance(v,str) and v.strip().lower() in {"","0","false","no","off","否","无"}: return False
    return bool(v)
def _get(obj,path,default=None):
    cur=obj
    for part in str(path or "").split("."):
        if isinstance(cur,dict) and part in cur: cur=cur[part]
        elif isinstance(cur,(list,tuple)):
            try: cur=cur[int(part)]
            except Exception: return default
        else: return default
    return cur


def _default_graph(name="新规则图"):
    return {"id":_id("graph"),"name":str(name)[:80],"enabled":False,"nodes":[],"links":[]}


def _example_special(name, typ):
    return {
        "id":_id("graph"),"name":name,"enabled":False,
        "nodes":[
            {"id":"event","type":typ,"x":60,"y":80,"params":{}},
            {"id":"out","type":"output","x":430,"y":80,"params":{"cooldown":2.0,"mode":"edge"}},
        ],
        "links":[{"from":"event","out":"value","to":"out","in":"in"}],
    }


def ensure_assets():
    B.DOC_DIR.mkdir(parents=True,exist_ok=True)
    if not GRAPH_FILE.exists():
        payload={"format":"coyote-visual-rules-v1","version":VERSION,"graphs":[
            _default_graph("示例：普通图（未启用）"),
            _example_special("示例：死亡专用","death"),
            _example_special("示例：昏迷专用","passed"),
        ]}
        GRAPH_FILE.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")


def _normalize(raw):
    raw=raw if isinstance(raw,dict) else {}; g=_default_graph(raw.get("name","规则图"))
    g["id"]=str(raw.get("id") or g["id"])[:100]; g["enabled"]=bool(raw.get("enabled",False))
    seen=set(); ns=[]
    for n in raw.get("nodes",[]) if isinstance(raw.get("nodes"),list) else []:
        if not isinstance(n,dict): continue
        nid=str(n.get("id") or _id())[:100]
        if nid in seen: continue
        seen.add(nid)
        ns.append({"id":nid,"type":str(n.get("type") or "comment")[:60],
                   "x":_num(n.get("x")),"y":_num(n.get("y")),
                   "params":n.get("params") if isinstance(n.get("params"),dict) else {}})
    g["nodes"]=ns; ids={n["id"] for n in ns}; ls=[]
    for e in raw.get("links",[]) if isinstance(raw.get("links"),list) else []:
        if not isinstance(e,dict): continue
        a,b=str(e.get("from") or ""),str(e.get("to") or "")
        if a in ids and b in ids and a!=b:
            ls.append({"from":a,"out":str(e.get("out") or "value")[:32],
                       "to":b,"in":str(e.get("in") or "in")[:32]})
    g["links"]=ls[:512]; return g


def _incoming(g,nid,port=None):
    return [e for e in g.get("links",[]) if e.get("to")==nid and (port is None or e.get("in")==port)]
def _node_map(g): return {n["id"]:n for n in g.get("nodes",[])}


def validate_graph(g):
    g=_normalize(g); special=[n for n in g["nodes"] if n["type"] in _SPECIAL_TYPES]
    if len(special)>1: return False,"死亡和昏迷必须分别放在独立规则图中；一张图只能有一个专用触发器。"
    if special:
        for n in g["nodes"]:
            if n is special[0]: continue
            if n["type"] in {"trigger","death","passed","changed"}:
                return False,"死亡/昏迷专用图不能混入其他事件触发规则。"
    for n in g["nodes"]:
        if n["type"]=="trigger" and str(n["params"].get("rule_key")) in _SPECIAL_KEYS:
            return False,"dead/passedOut 必须使用专用死亡/昏迷模块。"
        if n["type"]=="output" and not _incoming(g,n["id"],"in"):
            return False,"存在没有连接 in 条件的电击输出模块。"
    mapping=_node_map(g); visiting=set(); done=set()
    def visit(nid):
        if nid in done:return True
        if nid in visiting:return False
        visiting.add(nid)
        for e in _incoming(g,nid):
            if e["from"] in mapping and not visit(e["from"]): return False
        visiting.remove(nid); done.add(nid); return True
    if not all(visit(nid) for nid in mapping): return False,"规则图存在循环连线。"
    return True,"校验通过"


def load_graphs():
    ensure_assets()
    try: payload=json.loads(GRAPH_FILE.read_text(encoding="utf-8"))
    except Exception as exc:
        B.add_log("错误","图形化规则读取失败",repr(exc)); payload={}
    loaded=[_normalize(x) for x in (payload.get("graphs",[]) if isinstance(payload,dict) else [])[:128]]
    good=set(); errors=0
    for g in loaded:
        ok,_=validate_graph(g)
        if ok: good.add(g["id"])
        else: errors+=1
    with _LOCK:
        graphs.clear(); graphs.extend(loaded); valid_graph_ids.clear(); valid_graph_ids.update(good)
    with _RUNTIME_LOCK: runtime.clear()
    B.add_log("系统","图形化规则已加载",f"{len(loaded)} 张图，{errors} 张需修正")
    return len(loaded),errors


def save_graphs(items=None):
    items=_copy(items if items is not None else graphs); normalized=[_normalize(x) for x in items[:128]]; good=set()
    for g in normalized:
        ok,msg=validate_graph(g)
        if not ok:return False,f"{g['name']}：{msg}"
        good.add(g["id"])
    payload={"format":"coyote-visual-rules-v1","version":VERSION,"graphs":normalized}
    try:
        tmp=GRAPH_FILE.with_name(GRAPH_FILE.name+".tmp")
        tmp.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8"); tmp.replace(GRAPH_FILE)
        with _LOCK:
            graphs.clear(); graphs.extend(_copy(normalized)); valid_graph_ids.clear(); valid_graph_ids.update(good)
        return True,str(GRAPH_FILE)
    except Exception as exc:return False,str(exc)


def builtins_disabled():
    with _LOCK: snapshot=list(graphs); good=set(valid_graph_ids)
    return any(g.get("enabled") and g.get("id") in good and any(n["type"]=="disable_builtin" for n in g["nodes"]) for g in snapshot)


def _rt(gid,nid):
    with _RUNTIME_LOCK:return runtime.setdefault((gid,nid),{})
def _event_keys():
    v=getattr(_EVENTS,"keys",None); return v if isinstance(v,set) else set()


def _status(current,name):
    fallback=None; key=str(name or "Injury")
    for i,(raw,zh) in enumerate(getattr(B,"STATUS_ORDER",[])):
        if key in {raw,zh}: key=raw; fallback=i; break
    try:
        v=B.status_percent_for_rule(current,key,fallback); return 0.0 if v is None else float(v)
    except Exception:return 0.0


def _item(current,where,needle):
    needle=str(needle or "").strip().lower()
    if not needle:return False
    if where=="held":
        held=current.get("heldItem") or {}; return needle in str(held.get("name",current.get("heldName","")) or "").lower()
    key="backpackItems" if where=="backpack" else "pocketItems"
    vals=current.get(key)
    if vals is None: vals=(current.get("inventory") or {}).get(key,[])
    return isinstance(vals,list) and any(needle in str(v or "").lower() for v in vals)


def _eval(g,nid,current,previous,cache,stack):
    if nid in cache:return cache[nid]
    if nid in stack:return False
    n=_node_map(g).get(nid)
    if not n:return None
    stack.add(nid); typ=n["type"]; p=n.get("params",{})
    def values(port):return [_eval(g,e["from"],current,previous,cache,stack) for e in _incoming(g,nid,port)]
    def first(port,d=None):
        v=values(port); return v[0] if v else d
    if typ=="trigger": v=str(p.get("rule_key") or "") in _event_keys()
    elif typ=="death": v=bool(current.get("dead",False))
    elif typ=="passed": v=bool(current.get("passedOut",False))
    elif typ=="telemetry": v=_get(current,p.get("path","hp"),p.get("default",0))
    elif typ=="status": v=_status(current,p.get("name","Injury"))
    elif typ=="item": v=_item(current,p.get("where","held"),p.get("contains",""))
    elif typ=="changed":
        sentinel=object(); path=p.get("path","hp"); v=_get(current,path,sentinel)!=_get(previous,path,sentinel)
    elif typ=="constant": v=p.get("value",0)
    elif typ=="compare":
        a,b=first("a",p.get("a",0)),first("b",p.get("b",0)); op=str(p.get("op",">"))
        if op==">":v=_num(a)>_num(b)
        elif op==">=":v=_num(a)>=_num(b)
        elif op=="<":v=_num(a)<_num(b)
        elif op=="<=":v=_num(a)<=_num(b)
        elif op=="==":v=a==b or str(a)==str(b)
        elif op=="!=":v=not(a==b or str(a)==str(b))
        elif op=="contains":v=str(b).lower() in str(a).lower()
        else:v=False
    elif typ=="and":
        xs=values("in"); v=bool(xs) and all(_truth(x) for x in xs)
    elif typ=="or":v=any(_truth(x) for x in values("in"))
    elif typ=="not":v=not _truth(first("in",False))
    elif typ=="edge":
        active=_truth(first("in",False)); r=_rt(g["id"],nid); old=bool(r.get("active",False)); r["active"]=active
        mode=str(p.get("mode","rising")); v=active if mode=="while" else (old and not active if mode=="falling" else active and not old)
    elif typ=="cooldown":
        active=_truth(first("in",False)); r=_rt(g["id"],nid); now=time.monotonic(); sec=max(0,min(3600,_num(p.get("seconds",2),2)))
        v=bool(active and now-_num(r.get("last",-1e12),-1e12)>=sec)
        if v:r["last"]=now
    elif typ in {"intensity","duration","waveform","threshold","spike","random_waveform"}:v={"kind":typ,**p}
    elif typ in {"disable_builtin","comment","output"}:v=True
    else:v=False
    stack.remove(nid); cache[nid]=v; return v


def _config(g,out,current,previous,cache):
    cfg=B.default_rule(); cfg.update({"enabled":True,"thresholds":[],"spike_tiers":[],"spike_enabled":False}); pool=[]
    for port in ("intensity","duration","waveform","modifier"):
        for e in _incoming(g,out["id"],port):
            x=_eval(g,e["from"],current,previous,cache,set())
            if not isinstance(x,dict):continue
            k=x.get("kind")
            if k=="intensity":
                for dst,src in (("intensity_a","a"),("intensity_b","b"),("max_intensity_a","max_a"),("max_intensity_b","max_b")):
                    cfg[dst]=B.clamp_int(x.get(src,cfg.get(dst,5)))
                cfg["random_intensity"]=bool(x.get("random",False))
                for dst,src in (("random_min_a","min_a"),("random_max_a","max_rand_a"),("random_min_b","min_b"),("random_max_b","max_rand_b")):
                    cfg[dst]=B.clamp_int(x.get(src,cfg.get(dst,1)))
            elif k=="duration":
                cfg["play_time_a"]=B.clamp_duration(x.get("a",1000)); cfg["play_time_b"]=B.clamp_duration(x.get("b",1000))
            elif k=="waveform":
                wa=str(x.get("a","脉冲")); wb=str(x.get("b","脉冲")); cfg["waveform_a"]=wa if wa in B.COYOTE_WAVEFORMS else "脉冲"; cfg["waveform_b"]=wb if wb in B.COYOTE_WAVEFORMS else "脉冲"
            elif k=="threshold":
                cfg["thresholds"].append({"below":B.clamp_percent(x.get("below",50)),"add_a":B.clamp_int(x.get("add_a",0)),"add_b":B.clamp_int(x.get("add_b",0)),"waveform_a":str(x.get("waveform_a",B.TIER_WAVEFORM_INHERIT)),"waveform_b":str(x.get("waveform_b",B.TIER_WAVEFORM_INHERIT))})
            elif k=="spike":
                cfg["spike_enabled"]=True; cfg["spike_tiers"].append({"delta":max(.1,min(100,_num(x.get("delta",50),50))),"min_a":B.clamp_int(x.get("min_a",0)),"max_a":B.clamp_int(x.get("max_a",0)),"min_b":B.clamp_int(x.get("min_b",0)),"max_b":B.clamp_int(x.get("max_b",0))})
            elif k=="random_waveform":
                raw=x.get("pool",[]); raw=[q.strip() for q in raw.replace(";",",").split(",")] if isinstance(raw,str) else raw
                if isinstance(raw,list):pool.extend(q for q in raw if q in B.COYOTE_WAVEFORMS)
    cfg["thresholds"]=B.normalize_thresholds(cfg["thresholds"]); cfg["spike_tiers"]=B.normalize_spike_tiers(cfg["spike_tiers"])
    cfg["cooldown"]=B.clamp_cooldown(out.get("params",{}).get("cooldown",2)); return cfg,pool


def _send_graph(g,out,cfg,pool,value,delta,privileged):
    if B.peak_is_incapacitated() and not privileged:return False
    if not B.master_output_enabled:return False
    slot=B.get_slot_id()
    if not slot:return False
    r=_rt(g["id"],out["id"]); now=time.monotonic(); cd=B.clamp_cooldown(cfg.get("cooldown",2))
    if now-_num(r.get("last",-1e12),-1e12)<cd:return False
    r["last"]=now; info=B.calculate_rule_intensities(cfg,value,delta); ia,ib=info["final_a"],info["final_b"]
    da0,db0=B.clamp_duration(cfg.get("play_time_a",1000)),B.clamp_duration(cfg.get("play_time_b",1000)); da,db=B.resolve_rule_duration_ms(da0),B.resolve_rule_duration_ms(db0)
    wa,wb=(random.choice(pool),random.choice(pool)) if pool else (cfg.get("waveform_a","脉冲"),cfg.get("waveform_b","脉冲"))
    tier=info.get("tier")
    if tier:
        if tier.get("waveform_a") not in (None,B.TIER_WAVEFORM_INHERIT) and tier.get("waveform_a") in B.COYOTE_WAVEFORMS:wa=tier["waveform_a"]
        if tier.get("waveform_b") not in (None,B.TIER_WAVEFORM_INHERIT) and tier.get("waveform_b") in B.COYOTE_WAVEFORMS:wb=tier["waveform_b"]
    results=[]
    for ch,intensity,dur,wname in ((0,ia,da,wa),(1,ib,db,wb)):
        if intensity<=0:continue
        results.append(B.send_rpc("device.op",{"s":slot,"c":ch,"t":4,"v":intensity,"d":dur,"im":True}))
        results.append(B.send_rpc("device.op",{"s":slot,"c":ch,"t":0,"v":B.COYOTE_WAVEFORMS.get(wname,B.COYOTE_WAVEFORMS["脉冲"]),"d":dur,"im":True}))
    success=bool(results) and all(ok for ok,_ in results)
    try:
        with B.log_lock:
            B.output_count+=1; B.last_output={"event":g["name"],"change":"专用规则" if privileged else "图形条件成立","a_intensity":ia,"b_intensity":ib,"a_duration":da0,"b_duration":db0,"a_waveform":wa,"b_waveform":wb,"success":success,"visual_graph":True}
    except Exception:pass
    B.add_log("图形规则",g["name"],f"A={ia}/{da0}ms/{wa} | B={ib}/{db0}ms/{wb} | {'发送成功' if success else '发送失败'}")
    return success


def _special(g):return any(n["type"] in _SPECIAL_TYPES for n in g.get("nodes",[]))
def evaluate_graph(g,current,previous,privileged=False):
    if not g.get("enabled") or g.get("id") not in valid_graph_ids or _special(g)!=bool(privileged):return False
    if not privileged and B.peak_is_incapacitated(current):return False
    cache={}; sent=False
    for out in [n for n in g["nodes"] if n["type"]=="output"]:
        cond=[_eval(g,e["from"],current,previous,cache,set()) for e in _incoming(g,out["id"],"in")]; active=bool(cond) and all(_truth(x) for x in cond)
        cfg,pool=_config(g,out,current,previous,cache); r=_rt(g["id"],out["id"]); old=bool(r.get("active",False)); r["active"]=active
        mode=str(out.get("params",{}).get("mode","edge")).lower(); continuous=B.is_continuous_duration(cfg.get("play_time_a")) or B.is_continuous_duration(cfg.get("play_time_b"))
        if not (active if mode in {"while","repeat"} or continuous else active and not old):continue
        vi=_incoming(g,out["id"],"value"); di=_incoming(g,out["id"],"delta"); value=delta=None
        if vi:value=_num(_eval(g,vi[0]["from"],current,previous,cache,set()))
        if di:delta=abs(_num(_eval(g,di[0]["from"],current,previous,cache,set())))
        sent=_send_graph(g,out,cfg,pool,value,delta,privileged) or sent
    return sent
def evaluate_all(current,previous,privileged=False):
    with _LOCK:snapshot=list(graphs)
    return any(evaluate_graph(g,current,previous,privileged) for g in snapshot)


def _send_special_builtin(key,name,detail):
    cfg=B.get_rule_copy(key)
    if not cfg.get("enabled") or not B.master_output_enabled:return False
    slot=B.get_slot_id()
    if not slot or not B.rule_can_trigger(key,B.clamp_cooldown(cfg.get("cooldown",2))):return False
    info=B.calculate_rule_intensities(cfg,None,None); ia,ib=info["final_a"],info["final_b"]
    da0,db0=B.clamp_duration(cfg.get("play_time_a",1000)),B.clamp_duration(cfg.get("play_time_b",1000)); da,db=B.resolve_rule_duration_ms(da0),B.resolve_rule_duration_ms(db0)
    wa,wb=str(cfg.get("waveform_a","脉冲")),str(cfg.get("waveform_b","脉冲")); results=[]
    for ch,intensity,dur,wname in ((0,ia,da,wa),(1,ib,db,wb)):
        if intensity<=0:continue
        results.append(B.send_rpc("device.op",{"s":slot,"c":ch,"t":4,"v":intensity,"d":dur,"im":True}))
        results.append(B.send_rpc("device.op",{"s":slot,"c":ch,"t":0,"v":B.COYOTE_WAVEFORMS.get(wname,B.COYOTE_WAVEFORMS["脉冲"]),"d":dur,"im":True}))
    ok=bool(results) and all(x for x,_ in results); B.add_log("输出",name,f"{detail} | 死亡/昏迷专用通道 | {'发送成功' if ok else '发送失败'}"); return ok


def install_backend():
    global _BACKEND_INSTALLED
    if _BACKEND_INSTALLED:return
    _BACKEND_INSTALLED=True; ensure_assets()
    B.ensure_custom_rule_assets=ensure_assets; B.load_custom_rules=load_graphs; B.handle_custom_rules=lambda current,previous:None
    B.visual_rule_graphs=graphs; B.save_visual_rules=save_graphs; B.validate_visual_graph=validate_graph; B.visual_builtin_rules_disabled=builtins_disabled; B.VISUAL_RULE_FILE=GRAPH_FILE
    original_send=B.send_rule_output
    def send(rule_key,event_name,change_detail,current_value_pct=None,continuous=False,change_delta_pct=None):
        keys=getattr(_EVENTS,"keys",None)
        if isinstance(keys,set):keys.add(str(rule_key))
        if rule_key in _SPECIAL_KEYS and B.peak_is_incapacitated():return False
        if builtins_disabled():return False
        return original_send(rule_key,event_name,change_detail,current_value_pct=current_value_pct,continuous=continuous,change_delta_pct=change_delta_pct)
    B.send_rule_output=send
    original_handle=B.handle_game_rules
    def handle(current,previous):
        if not isinstance(current,dict) or not isinstance(previous,dict):return original_handle(current,previous)
        if current.get("localPlayer") is False or current.get("hasCharacter",True) is False or previous.get("hasCharacter",True) is False:return original_handle(current,previous)
        try:
            if current.get("packetSeq") is not None and previous.get("packetSeq") is not None and int(current["packetSeq"])<=int(previous["packetSeq"]):return original_handle(current,previous)
        except Exception:pass
        now=B.peak_is_incapacitated(current); was=B.peak_is_incapacitated(previous)
        if now:
            if not was:B.clear_device_output("角色死亡/昏迷：先清除普通输出，再执行专用规则"); B.add_log("系统","死亡/昏迷规则域","普通规则已锁定；仅死亡/昏迷专用规则可输出")
            _EVENTS.keys=set()
            try:
                if current.get("dead") and not previous.get("dead"):_EVENTS.keys.add("dead"); _send_special_builtin("dead","死亡","否 → 是")
                if current.get("passedOut") and not previous.get("passedOut"):_EVENTS.keys.add("passedOut"); _send_special_builtin("passedOut","昏迷","否 → 是")
                return evaluate_all(current,previous,True)
            finally:_EVENTS.keys=set()
        _EVENTS.keys=set()
        try:
            result=original_handle(current,previous); evaluate_all(current,previous,False); return result
        finally:_EVENTS.keys=set()
    B.handle_game_rules=handle


# ------------------------------ Qt editor ---------------------------------
def install_ui(UI):
    global _UI_INSTALLED
    if _UI_INSTALLED:return
    _UI_INSTALLED=True
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QBrush,QPainterPath,QPen
    from PySide6.QtWidgets import QGraphicsEllipseItem,QGraphicsItem,QGraphicsPathItem,QGraphicsRectItem,QGraphicsScene,QGraphicsTextItem,QGraphicsView,QInputDialog,QTreeWidget,QTreeWidgetItem

    SPECS={
        "death":("死亡（专用）",[],["value"]),"passed":("昏迷（专用）",[],["value"]),"trigger":("内置规则事件",[],["value"]),
        "telemetry":("遥测字段",[],["value"]),"status":("状态百分比",[],["value"]),"item":("物品匹配",[],["value"]),"changed":("字段变化",[],["value"]),"constant":("常量",[],["value"]),
        "compare":("比较",["a","b"],["value"]),"and":("AND",["in"],["value"]),"or":("OR",["in"],["value"]),"not":("NOT",["in"],["value"]),"edge":("上升沿 / 持续",["in"],["value"]),"cooldown":("冷却",["in"],["value"]),
        "intensity":("强度",[],["config"]),"duration":("持续时间",[],["config"]),"waveform":("波形",[],["config"]),"threshold":("百分比档位",[],["config"]),"spike":("瞬时变化加强",[],["config"]),"random_waveform":("随机波形",[],["config"]),
        "disable_builtin":("禁用软件内置规则",[],[]),"output":("电击输出",["in","intensity","duration","waveform","modifier","value","delta"],[]),"comment":("备注",[],[]),
    }
    def defaults(t):
        return _copy({
            "trigger":{"rule_key":"hp"},"telemetry":{"path":"hp","default":0},"status":{"name":"Injury"},"item":{"where":"held","contains":""},"changed":{"path":"hp"},"constant":{"value":0},"compare":{"op":">","a":0,"b":0},"edge":{"mode":"rising"},"cooldown":{"seconds":2.0},
            "intensity":{"a":5,"b":5,"max_a":10,"max_b":10,"random":False,"min_a":1,"max_rand_a":5,"min_b":1,"max_rand_b":5},"duration":{"a":1000,"b":1000},"waveform":{"a":"脉冲","b":"脉冲"},
            "threshold":{"below":50,"add_a":2,"add_b":2,"waveform_a":B.TIER_WAVEFORM_INHERIT,"waveform_b":B.TIER_WAVEFORM_INHERIT},"spike":{"delta":50,"min_a":2,"max_a":5,"min_b":2,"max_b":5},"random_waveform":{"pool":",".join(B.waveform_names())},"output":{"cooldown":2.0,"mode":"edge"},"comment":{"text":"备注"},
        }.get(t,{}))

    class Port(QGraphicsEllipseItem):
        def __init__(self,node,name,out,index):
            super().__init__(-5,-5,10,10,node); self.node=node; self.name=name; self.out=out; self.setBrush(QBrush(UI.QColor("#5B8CFF" if out else "#46C58A"))); self.setZValue(5); self.setPos(190 if out else 0,50+index*22)
        def mousePressEvent(self,e):
            ed=self.node.ed
            if self.out:ed.pending=self; ed.status.setText(f"已选择输出：{self.node.title}/{self.name}；请点击目标输入端口")
            elif ed.pending:ed.connect(ed.pending,self); ed.pending=None
            e.accept()
        def center(self):return self.mapToScene(QPointF(0,0))
    class Edge(QGraphicsPathItem):
        def __init__(self,ed,data,a,b):super().__init__(); self.ed=ed; self.data=data; self.a=a; self.b=b; self.setPen(QPen(UI.QColor("#7AA2FF"),2)); self.setZValue(-1); self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable,True); self.update_path()
        def update_path(self):
            a,b=self.a.center(),self.b.center(); dx=max(40,abs(b.x()-a.x())*.5); p=QPainterPath(a); p.cubicTo(QPointF(a.x()+dx,a.y()),QPointF(b.x()-dx,b.y()),b); self.setPath(p)
    class Node(QGraphicsRectItem):
        def __init__(self,ed,data):
            title,ins,outs=SPECS.get(data["type"],(data["type"],[],[])); h=max(86,60+22*max(len(ins),len(outs),1)); super().__init__(0,0,190,h); self.ed=ed; self.data=data; self.title=title; self.ins={}; self.outs={}; self.setBrush(QBrush(UI.QColor("#172238"))); self.setPen(QPen(UI.QColor("#3D516D"),1.5)); self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable|QGraphicsItem.GraphicsItemFlag.ItemIsSelectable|QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges); txt=QGraphicsTextItem(title,self); txt.setDefaultTextColor(UI.QColor("#F1F5FB")); txt.setPos(8,4)
            for i,n in enumerate(ins):self.ins[n]=Port(self,n,False,i)
            for i,n in enumerate(outs):self.outs[n]=Port(self,n,True,i)
            self.setPos(_num(data.get("x")),_num(data.get("y")))
        def itemChange(self,c,v):
            if c==QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
                self.data["x"],self.data["y"]=self.pos().x(),self.pos().y(); self.ed.update_edges(self.data["id"])
            if c==QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged and bool(v):self.ed.show_props(self)
            return super().itemChange(c,v)
    class View(QGraphicsView):
        def __init__(self,ed,scene):super().__init__(scene); self.ed=ed; self.setDragMode(QGraphicsView.DragMode.RubberBandDrag); self.setSceneRect(-3000,-2000,6000,4000)
        def wheelEvent(self,e):self.scale(1.12 if e.angleDelta().y()>0 else 1/1.12,1.12 if e.angleDelta().y()>0 else 1/1.12)
        def keyPressEvent(self,e):
            if e.key() in (UI.Qt.Key.Key_Delete,UI.Qt.Key.Key_Backspace):self.ed.delete_selected()
            else:super().keyPressEvent(e)
    class Editor(UI.QWidget):
        def __init__(self,window):super().__init__(); self.window=window; self.items=[]; self.current=None; self.nodes={}; self.edges=[]; self.pending=None; self.selected=None; self.build(); self.reload()
        def build(self):
            root=UI.QVBoxLayout(self); top=UI.QHBoxLayout(); self.combo=UI.QComboBox(); self.combo.currentIndexChanged.connect(self.load_index); self.enabled=UI.QCheckBox("启用当前图"); self.enabled.toggled.connect(lambda x:self.current.__setitem__("enabled",bool(x)) if self.current else None)
            for text,fn in (("新建图",self.new),("重命名",self.rename),("删除图",self.delete_graph)):
                b=UI.QPushButton(text); b.clicked.connect(fn); top.addWidget(b)
            top.insertWidget(0,self.combo,1); top.addWidget(self.enabled); save=UI.QPushButton("校验并保存"); save.setObjectName("primary"); save.clicked.connect(self.save); top.addWidget(save); root.addLayout(top)
            split=UI.QSplitter(UI.Qt.Orientation.Horizontal); self.palette=QTreeWidget(); self.palette.setHeaderHidden(True); self.populate(); self.palette.itemDoubleClicked.connect(self.add); split.addWidget(self.palette)
            self.scene=QGraphicsScene(self); self.view=View(self,self.scene); split.addWidget(self.view)
            right=UI.QWidget(); rl=UI.QVBoxLayout(right); self.props=UI.QTableWidget(0,2); self.props.setHorizontalHeaderLabels(["参数","值"]); self.props.horizontalHeader().setSectionResizeMode(1,UI.QHeaderView.ResizeMode.Stretch); rl.addWidget(self.props,1); apply=UI.QPushButton("应用属性"); apply.clicked.connect(self.apply); rl.addWidget(apply)
            helpbox=UI.QTextEdit(); helpbox.setReadOnly(True); helpbox.setMaximumHeight(230)
            try:helpbox.setMarkdown(DOC_FILE.read_text(encoding="utf-8"))
            except Exception:helpbox.setPlainText("模块连线规则保存为 visual_rules.json。死亡/昏迷必须使用专用独立规则图。")
            rl.addWidget(helpbox); split.addWidget(right); split.setSizes([260,900,340]); root.addWidget(split,1); self.status=UI.QLabel("先点输出端口，再点输入端口连线；Delete 删除选中模块/连线；滚轮缩放。"); self.status.setObjectName("muted"); root.addWidget(self.status)
        def leaf(self,parent,text,t,p=None):it=QTreeWidgetItem(parent,[text]); it.setData(0,UI.Qt.ItemDataRole.UserRole,{"type":t,"params":p or {}})
        def populate(self):
            sp=QTreeWidgetItem(self.palette,["专用事件"]); self.leaf(sp,"死亡（专用）","death"); self.leaf(sp,"昏迷（专用）","passed")
            ev=QTreeWidgetItem(self.palette,["当前软件全部规则事件"]); seen=set()
            for key,display,_,trigger in B.RULE_META:
                if key in _SPECIAL_KEYS or key in seen:continue
                seen.add(key); self.leaf(ev,f"{display} · {trigger}","trigger",{"rule_key":key})
            co=QTreeWidgetItem(self.palette,["数据 / 条件"])
            for a,t in (("遥测字段","telemetry"),("状态百分比","status"),("物品匹配","item"),("字段变化","changed"),("常量","constant"),("比较","compare"),("AND","and"),("OR","or"),("NOT","not"),("上升沿 / 持续","edge"),("冷却","cooldown")):self.leaf(co,a,t)
            pa=QTreeWidgetItem(self.palette,["输出参数"])
            for a,t in (("强度","intensity"),("持续时间","duration"),("波形","waveform"),("百分比档位","threshold"),("瞬时变化加强","spike"),("随机波形","random_waveform")):self.leaf(pa,a,t)
            ac=QTreeWidgetItem(self.palette,["控制 / 动作"]); self.leaf(ac,"电击输出","output"); self.leaf(ac,"禁用软件内置规则（存在即生效）","disable_builtin"); self.leaf(ac,"备注","comment"); self.palette.expandAll()
        def reload(self):load_graphs(); self.items=_copy(graphs); self.refresh()
        def refresh(self,selected=None):
            self.combo.blockSignals(True); self.combo.clear(); [self.combo.addItem(g["name"],g["id"]) for g in self.items]; self.combo.blockSignals(False); i=self.combo.findData(selected) if selected else 0; i=0 if i<0 else i
            if self.items:self.combo.setCurrentIndex(i); self.load_index(i)
        def load_index(self,i):
            if i<0 or i>=len(self.items):return
            self.current=self.items[i]; self.enabled.blockSignals(True); self.enabled.setChecked(bool(self.current.get("enabled"))); self.enabled.blockSignals(False); self.rebuild()
        def new(self):
            name,ok=QInputDialog.getText(self,"新建规则图","名称",text="新规则图")
            if ok:self.items.append(_default_graph(name or "新规则图")); self.refresh(self.items[-1]["id"])
        def rename(self):
            if not self.current:return
            name,ok=QInputDialog.getText(self,"重命名","名称",text=self.current["name"])
            if ok and name.strip():self.current["name"]=name.strip()[:80]; self.refresh(self.current["id"])
        def delete_graph(self):
            if not self.current:return
            gid=self.current["id"]; self.items=[g for g in self.items if g["id"]!=gid] or [_default_graph()]; self.refresh()
        def add(self,item,column=0):
            d=item.data(0,UI.Qt.ItemDataRole.UserRole)
            if not isinstance(d,dict) or not self.current:return
            p=defaults(d["type"]); p.update(d.get("params",{})); n={"id":_id(),"type":d["type"],"x":80+len(self.current["nodes"])*15,"y":80+len(self.current["nodes"])*15,"params":p}; self.current["nodes"].append(n); self.rebuild()
        def rebuild(self):
            self.scene.clear(); self.nodes={}; self.edges=[]; self.pending=None
            if not self.current:return
            for n in self.current["nodes"]:x=Node(self,n); self.nodes[n["id"]]=x; self.scene.addItem(x)
            for e in self.current["links"]:self.make_edge(e)
        def make_edge(self,e):
            a,b=self.nodes.get(e["from"]),self.nodes.get(e["to"])
            if not a or not b or e["out"] not in a.outs or e["in"] not in b.ins:return
            x=Edge(self,e,a.outs[e["out"]],b.ins[e["in"]]); self.edges.append(x); self.scene.addItem(x)
        def connect(self,a,b):
            if not self.current or a.node is b.node:return
            multi=b.name=="modifier" or (b.name=="in" and b.node.data["type"] in {"and","or","output"})
            if not multi:self.current["links"]=[e for e in self.current["links"] if not(e["to"]==b.node.data["id"] and e["in"]==b.name)]
            e={"from":a.node.data["id"],"out":a.name,"to":b.node.data["id"],"in":b.name}
            if e not in self.current["links"]:self.current["links"].append(e)
            self.rebuild()
        def update_edges(self,nid):
            for e in self.edges:
                if nid in (e.data["from"],e.data["to"]):e.update_path()
        def delete_selected(self):
            if not self.current:return
            selected=list(self.scene.selectedItems()); ids={x.data["id"] for x in selected if isinstance(x,Node)}; links=[x.data for x in selected if isinstance(x,Edge)]
            self.current["nodes"]=[n for n in self.current["nodes"] if n["id"] not in ids]; self.current["links"]=[e for e in self.current["links"] if e["from"] not in ids and e["to"] not in ids and e not in links]; self.rebuild()
        def show_props(self,node):
            self.selected=node; p=node.data.get("params",{}); self.props.setRowCount(len(p))
            for r,(k,v) in enumerate(p.items()):
                ki=UI.QTableWidgetItem(k); ki.setFlags(ki.flags()&~UI.Qt.ItemFlag.ItemIsEditable); self.props.setItem(r,0,ki); self.props.setItem(r,1,UI.QTableWidgetItem(json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else ("true" if v is True else "false" if v is False else str(v))))
        def apply(self):
            if not self.selected:return
            p=self.selected.data.setdefault("params",{})
            for r in range(self.props.rowCount()):
                k=self.props.item(r,0).text(); text=self.props.item(r,1).text().strip(); old=p.get(k)
                try:
                    if isinstance(old,bool):v=text.lower() in {"1","true","yes","on","是"}
                    elif isinstance(old,int):v=int(float(text))
                    elif isinstance(old,float):v=float(text)
                    elif isinstance(old,(list,dict)):v=json.loads(text)
                    else:v=text
                    p[k]=v
                except Exception:pass
        def save(self):
            bad=[]
            for g in self.items:
                ok,msg=validate_graph(g)
                if not ok:bad.append(f"{g['name']}：{msg}")
            if bad:self.window.msg_warning("规则图校验失败","请修正规则图后再保存。","\n".join(bad[:12])); return
            ok,msg=save_graphs(self.items)
            if ok:self.window.msg_info("图形化规则已保存","所有规则图已写入 visual_rules.json。",str(GRAPH_FILE))
            else:self.window.msg_error("规则图保存失败","无法写入 visual_rules.json。",msg)

    Base=UI.Window
    class Window(Base):
        def build_custom_code(self):
            l=UI.QVBoxLayout(self.code_page); l.setContentsMargins(4,4,4,4); box,bl=self.panel("图形化规则"); note=UI.QLabel("自定义规则已改为模块 + 连线，保存为 visual_rules.json；不再执行用户编写的 .py。死亡/昏迷必须使用专用独立规则图。"); note.setObjectName("muted"); note.setWordWrap(True); bl.addWidget(note); l.addWidget(box); self.visual_rule_editor=Editor(self); l.addWidget(self.visual_rule_editor,1)
        def __init__(self):
            super().__init__()
            try:
                i=self.page_indices["code"]; b=self.nav_buttons[i]; icon=str(b.property("nav_icon") or "⌘"); b.setProperty("nav_source_label","图形化规则"); b.setProperty("fullText",f"{icon}   图形化规则")
                if not self._sidebar_collapsed:b.setText(b.property("fullText"))
            except Exception:pass
        def switch_page(self,i):
            super().switch_page(i)
            try:
                if i==self.page_indices.get("code"):self.header_title.setText("图形化规则"); self.header_subtitle.setText("模块 · 端口 · 连线 · JSON · 死亡/昏迷专用规则域")
            except Exception:pass
    UI.Window=Window
