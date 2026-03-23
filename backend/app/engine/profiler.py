import duckdb
import pandas as pd

def get_csv_stats(file_path: str):
    """
    Uses DuckDB to compute statistics for a CSV file.
    Stats include: nulls, min, max, and cardinality (unique count).
    """
    # Use DuckDB's SUMMARIZE to get comprehensive column stats
    query = f"SUMMARIZE SELECT * FROM read_csv_auto('{file_path}')"
    stats_df = duckdb.query(query).df()
    
    # Select and rename relevant columns to match the request
    # SUMMARIZE returns: column_name, column_type, min, max, approx_unique, avg, std, q25, q50, q75, count, null_percentage
    # We will derive null_count from null_percentage and total count
    
    # Requery count to be precise
    count_query = f"SELECT COUNT(*) as total FROM read_csv_auto('{file_path}')"
    total_rows = duckdb.query(count_query).fetchone()[0]
    
    result = []
    for _, row in stats_df.iterrows():
        # Calculate null count from percentage
        null_count = int(round((row['null_percentage'] / 100.0) * total_rows))
        
        # Calculate variance as std squared, handle NaN
        std_val = row['std']
        if pd.notna(std_val) and std_val != 'nan':
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
        
        result.append({
            "column": row['column_name'],
            "type": row['column_type'],
            "nulls": null_count,
            "null_value_percent": row['null_percentage'],
            "min": str(row['min']),
            "max": str(row['max']),
            "cardinality": int(row['approx_unique']), # approx_unique is sufficient for cardinality
            "sd": sd,
            "variance": variance,
        })
        
    return {
        "total_rows": total_rows,
        "columns": result
    }
