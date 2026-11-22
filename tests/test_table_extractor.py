from app.services.ingestion.table_extractor import table_to_text


def test_joins_cells_with_pipe_and_rows_with_newline():
    table = [["Model", "Acc"], ["Qwen", "41"]]
    assert table_to_text(table) == "Model | Acc\nQwen | 41"


def test_none_cells_become_empty_strings():
    table = [["A", None, "C"]]
    assert table_to_text(table) == "A |  | C"


def test_cell_whitespace_is_stripped():
    table = [[" Model ", "Acc\n"]]
    assert table_to_text(table) == "Model | Acc"
