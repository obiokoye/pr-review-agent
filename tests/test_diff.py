from reviewer.diff import chunk_files, filter_files, parse_unified_diff


def test_parses_files_and_new_line_numbers(sql_diff):
    [f] = parse_unified_diff(sql_diff)
    assert f.path == "app/routes/users.py"
    assert f.additions == 15 and f.deletions == 0
    assert f.line_text(20).strip().startswith('sql = f"SELECT')
    assert 5 in f.added_lines and 12 not in f.added_lines  # line 12 is context
    assert 12 in f.commentable_lines


def test_removed_lines_are_not_commentable(laravel_diff):
    [f] = parse_unified_diff(laravel_diff)
    assert f.deletions == 1
    assert f.commentable_lines == set(range(8, 35))
    assert "Order::create($request->all())" in f.line_text(23)


def test_render_prefixes_new_line_numbers(sql_diff):
    [f] = parse_unified_diff(sql_diff)
    rendered = f.render()
    assert rendered.startswith("### File: app/routes/users.py")
    assert "   20 +     sql = f\"SELECT" in rendered


def test_filter_skips_lockfiles_and_deleted_files(clean_diff):
    deleted = "diff --git a/old.py b/old.py\ndeleted file mode 100644\n--- a/old.py\n+++ /dev/null\n@@ -1 +0,0 @@\n-x = 1\n"
    files = parse_unified_diff(clean_diff + deleted)
    keep, skipped = filter_files(files)
    assert [f.path for f in keep] == ["app/utils/slug.py"]
    assert skipped == ["package-lock.json", "old.py"]


def test_filter_accepts_extra_ignore_globs(clean_diff):
    keep, skipped = filter_files(parse_unified_diff(clean_diff), ["app/utils/*"])
    assert keep == [] and "app/utils/slug.py" in skipped


def test_new_file_and_binary_flags():
    text = (
        "diff --git a/new.py b/new.py\nnew file mode 100644\n--- /dev/null\n+++ b/new.py\n@@ -0,0 +1,2 @@\n+a = 1\n+b = 2\n"
        "diff --git a/logo.png b/logo.png\nBinary files a/logo.png and b/logo.png differ\n"
    )
    new, binary = parse_unified_diff(text)
    assert new.is_new and new.added_lines == {1, 2}
    assert binary.is_binary


def test_chunking_respects_budget(sql_diff, laravel_diff):
    files = parse_unified_diff(sql_diff + laravel_diff)
    assert len(chunk_files(files, 100_000)) == 1
    assert len(chunk_files(files, 500)) == 2
