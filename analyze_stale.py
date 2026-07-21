import sqlite3
import datetime

DB_PATH = 'data/nodos.db'

def analyze_gaps(threshold_seconds=120):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    
    # Get all nodes
    cursor.execute("SELECT DISTINCT box_id FROM sensor_readings")
    nodes = [row['box_id'] for row in cursor.fetchall()]
    
    for node in nodes:
        print(f"Analyzing node {node}...")
        cursor.execute("SELECT received_at FROM sensor_readings WHERE box_id = ? ORDER BY received_at ASC", (node,))
        rows = cursor.fetchall()
        
        stale_count = 0
        max_gap = 0
        total_readings = len(rows)
        
        if total_readings < 2:
            print(f"  Not enough readings ({total_readings}).")
            continue
            
        prev_time = datetime.datetime.fromisoformat(rows[0]['received_at'])
        
        for i in range(1, len(rows)):
            curr_time = datetime.datetime.fromisoformat(rows[i]['received_at'])
            gap = (curr_time - prev_time).total_seconds()
            
            if gap > threshold_seconds:
                stale_count += 1
            if gap > max_gap:
                max_gap = gap
                
            prev_time = curr_time
            
        print(f"  Total readings: {total_readings}")
        print(f"  Times went stale (> {threshold_seconds}s gap): {stale_count}")
        print(f"  Max gap between readings: {max_gap}s")
        print()
    
    conn.close()

if __name__ == '__main__':
    analyze_gaps()
