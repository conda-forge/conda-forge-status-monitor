#!/usr/bin/env python
import os
import json
import tempfile
import subprocess

import requests


# first pull down the data
latest_data = requests.get(
    "https://cf-action-counter.herokuapp.com/db").json()

# run git config
subprocess.run(
    "git config --global user.email 'circle_worker@email.com'",
    shell=True,
    check=True,
)
subprocess.run(
    "git config --global user.name 'circle worker'",
    shell=True,
    check=True,
)

# now update the repo
with tempfile.TemporaryDirectory() as tmpdir:
    os.chdir(tmpdir)
    subprocess.run(
        ("git clone --depth=1 "
         "https://github.com/regro/cf-action-counter-db.git"),
        shell=True,
        check=True,
    )

    os.chdir("cf-action-counter-db")
    os.makedirs("data", exist_ok=True)

    if os.path.exists("data/latest.json"):
        with open("data/latest.json", "r") as fp:
            old_data = json.load(fp)

        back_stamp = list(old_data["github-actions"]["rates"].keys())[-1]
        back_pth = "data/data_%s.json" % back_stamp
        with open(back_pth, "w") as fp:
            json.dump(old_data, fp)

        subprocess.run(
            "git add %s" % back_pth,
            shell=True,
            check=True,
        )

    with open("data/latest.json", "w") as fp:
        json.dump(latest_data, fp)

    subprocess.run(
        ["git add data/latest.json"],
        shell=True,
        check=True,
    )

    stat = subprocess.run(
        ["git status"],
        shell=True,
        check=True,
        capture_output=True,
    )
    status = stat.stdout.decode('utf-8')
    print(status)

    if "nothing to commit" not in status:
        subprocess.run(
            ["git commit -m 'hourly data update'"],
            shell=True,
            check=True,
        )

        subprocess.run(
            ["git push"],
            shell=True,
            check=True,
        )
