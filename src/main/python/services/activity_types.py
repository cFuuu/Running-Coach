"""活動類型（`activities.activity_type`）的家族集合——單一真實來源。

值為 Garmin Connect 在 `activityType.typeKey` 送出的字串，經
`garmin_sync_runner.normalize_activity()`／`garmin_export_parser` 原樣存入
`activities.activity_type`，本專案不另行改名。

⚠️ **這裡的集合彼此語意不同，不可互相取代**。之所以集中在同一個模組，
正是為了讓「跑步家族」與「配速可比較的跑步」的差別看得見——先前這兩者
分散在三個檔案裡，看起來像是同一份定義被抄了三次而其中一份漏了項目。
"""

from __future__ import annotations

# 視為「跑步類」的活動。用於篩選要納入跑步相關統計與分析的活動。
# 可調整的參數而非寫死的假設——之後要納入其他跑步子類型時擴充這裡即可。
RUNNING_ACTIVITY_TYPES: frozenset[str] = frozenset(
    {"running", "treadmill_running", "track_running", "trail_running"}
)

# 視為「重訓類」的活動。
STRENGTH_ACTIVITY_TYPES: frozenset[str] = frozenset({"strength_training"})

# 配速**互相可比較**的跑步活動——比 RUNNING_ACTIVITY_TYPES 少了 trail_running。
#
# ⚠️ 少掉 trail 是刻意的，不是遺漏：越野跑的配速由地形主導，與路跑／跑步機／
# 田徑場的配速不在同一個尺度上，混在一起算「近期平均配速」基準會失真。
# 需要「所有跑步」時用 RUNNING_ACTIVITY_TYPES，需要「配速可比」時用這個。
PACE_COMPARABLE_RUNNING_TYPES: frozenset[str] = frozenset(
    {"running", "treadmill_running", "track_running"}
)
