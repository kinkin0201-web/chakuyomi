# -*- coding: utf-8 -*-
"""学習済みLightGBMを依存ゼロのJSONへ書き出す。

38本の決定木は素朴なif分岐で表現できる。これをJSONにすれば
lightgbm不要(=重い依存なし)で推論でき、静的ホスティングや
無料枠のサーバレスでも動かせる。
"""
import json, lightgbm as lgb

def compact(node):
    """dump_modelの木を最小構造に変換する。"""
    if "leaf_value" in node:
        return {"v": node["leaf_value"]}
    is_cat = node.get("decision_type") == "=="
    out = {
        "f": node["split_feature"],
        "c": is_cat,
        "d": bool(node.get("default_left", True)),
        "l": compact(node["left_child"]),
        "r": compact(node["right_child"]),
    }
    if is_cat:
        # カテゴリ分割: 閾値は '0||2||5' 形式の集合
        out["s"] = [int(float(x)) for x in str(node["threshold"]).split("||")]
    else:
        out["t"] = float(node["threshold"])
    return out

from train_model import CAT_FEATURES as CAT_ORDER
b = lgb.Booster(model_file="kyotei_model.txt")
d = b.dump_model()
# 学習時のカテゴリ順序を保存する。
# これが無いと推論側で別のコード値になり結果がずれる。
cat_names = [c for c in b.feature_name() if c in CAT_ORDER]
out = {
    "features": b.feature_name(),
    "categories": dict(zip(cat_names, b.pandas_categorical or [])),
    "trees": [compact(t["tree_structure"]) for t in d["tree_info"]],
}
with open("model.json", "w") as f:
    json.dump(out, f, separators=(",", ":"))
print(f"書き出し完了: {len(out['trees'])}本, 特徴量{len(out['features'])}個")
