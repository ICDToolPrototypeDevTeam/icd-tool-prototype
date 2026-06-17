"""
merge/ 模块统一入口。

提供：
- merge_best_chunks(best_results)：合并所有 chunk 的最佳候选
- merge_model_candidates(candidates, model_name)：合并某个模型在所有 chunk 的候选
"""

from app.merge.merger import merge_best_chunks, merge_model_candidates

__all__ = ["merge_best_chunks", "merge_model_candidates"]
