import sqlite3, os
db = sqlite3.connect("/home/gguo/code/github/clawhubS/data/experiment1_split/merged.sqlite3")

# Get a sample text_path with /home/gguo/code/clawhubS prefix
row = db.execute("SELECT text_path FROM skill_files WHERE text_path LIKE '/home/gguo/code/clawhubS%' LIMIT 1").fetchone()
tp = row[0]
print(f"text_path: {tp}")
print(f"exists: {os.path.exists(tp)}")
print(f"islink /home/gguo/code/clawhubS: {os.path.islink('/home/gguo/code/clawhubS')}")

# The issue: os.path.exists follows symlinks, but the symlink might not
# resolve the full path correctly. Let's check step by step
parts = tp.split("/")
for i in range(1, len(parts)):
    subpath = "/" + "/".join(parts[1:i+1])
    exists = os.path.exists(subpath)
    is_link = os.path.islink(subpath)
    if not exists:
        print(f"BREAKS at: {subpath}")
        break
    if is_link:
        print(f"symlink: {subpath} -> {os.readlink(subpath)}")

db.close()
