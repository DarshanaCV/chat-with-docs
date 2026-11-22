def table_to_text(table):
    rows=[]
    for row in table:
        row=[str(cell).strip() if cell else "" for cell in row]
        rows.append(" | ".join(row))
    return "\n".join(rows)
