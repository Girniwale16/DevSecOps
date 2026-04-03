import duckdb
import pandas as pd


def get_csv_stats(file_path: str, sample_rows: int | None = None):
    """
    Uses DuckDB to compute statistics for a CSV file.
    Stats include: nulls, min, max, and cardinality (unique count).
    """
    conn = duckdb.connect()
    try:
        sample_limit = max(1, int(sample_rows)) if sample_rows else None
        if sample_limit:
            query = f"SUMMARIZE SELECT * FROM (SELECT * FROM read_csv_auto(?) LIMIT {sample_limit})"
        else:
            query = "SUMMARIZE SELECT * FROM read_csv_auto(?)"
        stats_df = conn.execute(query, (file_path,)).df()

        if sample_limit:
            total_rows = None
            observed_rows = int(
                conn.execute(
                    f"SELECT COUNT(*) as total FROM (SELECT * FROM read_csv_auto(?) LIMIT {sample_limit})",
                    (file_path,),
                ).fetchone()[0]
            )
        else:
            total_rows = int(conn.execute("SELECT COUNT(*) as total FROM read_csv_auto(?)", (file_path,)).fetchone()[0])
            observed_rows = total_rows

        profile_rows = observed_rows if sample_limit else total_rows

        # SUMMARIZE returns: column_name, column_type, min, max, approx_unique, avg, std, q25, q50, q75, count, null_percentage
        # We derive null_count from null_percentage and profiled row count.
        result = []
        for _, row in stats_df.iterrows():
            null_count = int(round((row["null_percentage"] / 100.0) * max(profile_rows or 0, 0)))

            std_val = row["std"]
            if pd.notna(std_val) and std_val != "nan":
                try:
                    std_float = float(std_val)
                    variance = std_float ** 2
                    sd = std_float
                except ValueError:
                    variance = None
                    sd = None
            else:
                variance = None
                sd = None

            result.append(
                {
                    "column": row["column_name"],
                    "type": row["column_type"],
                    "nulls": null_count,
                    "null_value_percent": row["null_percentage"],
                    "min": str(row["min"]),
                    "max": str(row["max"]),
                    "cardinality": int(row["approx_unique"]),
                    "sd": sd,
                    "variance": variance,
                }
            )

        return {
            "total_rows": total_rows,
            "profiled_rows": profile_rows,
            "profile_mode": "sampled" if sample_limit else "full",
            "columns": result,
        }
    finally:
        conn.close()
