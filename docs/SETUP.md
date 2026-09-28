# Setup Guide — WSL2 Workspace + GitHub Repository

Project: Adaptive and Parallel DNA Trace Reconstruction (BBS → ITR)
Machine: Intel i5-10210U (4 physical / 8 logical cores), 20 GB RAM, Windows 11 Pro

This guide takes you from a bare Windows machine to a working Linux (WSL2) workspace
with the project repository connected to a **public** GitHub repo.

> Why WSL2 and not the existing Linux VM or plain Windows?
> - WSL2 sees all 8 logical cores at near-native speed; a VirtualBox/VMware VM gets a
>   subset of virtual cores and adds overhead, which would distort PDC timing numbers.
> - Colab is Linux, so the code must run on Linux anyway. One OS = one set of bugs.
> - Process launch is much cheaper on Linux than Windows; our pipeline launches many
>   native BBS/ITR processes, so Windows would inflate "orchestration overhead".
> You do **not** need the separate Linux VM for this project.

Where the project will live:

| What | Where |
|---|---|
| Code + git repo | `~/projects/DNA-Reconstruction` inside WSL (Linux filesystem, fast) |
| Planning docs / PDFs | stay in `OneDrive\Desktop\DNA Computing` (Windows) |
| Large data + results | inside the WSL repo folder, git-ignored, never in OneDrive |

Do **not** put the repo under `/mnt/c/...` (the Windows drive seen from Linux). File
access across that boundary is ~10× slower and OneDrive would try to sync build output.

---

## Part 1 — Install Ubuntu in WSL2 (on the E: drive, not the SSD)

WSL2 is already enabled on this machine (Docker Desktop uses it), so no reboot should be needed.

**Disk plan.** C: is the 256 GB NVMe SSD with only ~26 GB free. E: is on the 1 TB HDD with
~248 GB free. The whole Linux system is stored in one virtual-disk file (`ext4.vhdx`), and
we put that file on **E:**, so the SSD is barely touched.

Expected size of the Linux disk for this project: **about 5–7 GB in total**

| Component | Approx. size |
|---|---|
| Ubuntu 24.04 base | 1.5–2 GB |
| build-essential (g++, make) | 0.3 GB |
| Rust toolchain (rustup) | 1.2–1.5 GB |
| BBS build (`target/` folder) | 0.5–1 GB |
| Python venv (numpy, pandas, matplotlib, scikit-learn) | 0.5 GB |
| Microsoft dataset + synthetic data + results | < 1 GB |

The HDD is slower than the SSD for *building* and *installing* (minutes, not seconds). It does
**not** affect the experiment timings: BBS/ITR are CPU-bound and their input files are small
enough to stay in RAM cache after the first read (this is also why every timing run has an
unmeasured warm-up).

1. Create the folder on E: and open **PowerShell as Administrator**
   (Start → type "PowerShell" → Run as administrator):
   ```powershell
   mkdir E:\WSL
   ```
2. Install Ubuntu 24.04 directly onto E:
   ```powershell
   wsl --install -d Ubuntu-24.04 --location E:\WSL\Ubuntu-24.04
   ```
   If your WSL says `--location` is not recognized, install normally and then move it:
   ```powershell
   wsl --install -d Ubuntu-24.04
   # (finish step 3 below first, then close the Ubuntu window)
   wsl --shutdown
   wsl --manage Ubuntu-24.04 --move E:\WSL\Ubuntu-24.04
   ```
3. When it finishes, an Ubuntu window opens and asks for a **Linux username and password**.
   Pick something short (e.g. `soban`). This password is used for `sudo`; it does not need
   to match your Windows password.
4. Make Ubuntu the default distro (so plain `wsl` opens it, not docker-desktop):
   ```powershell
   wsl --set-default Ubuntu-24.04
   wsl -l -v
   ```
   You should see `* Ubuntu-24.04   Running   2`.
5. **Skip the sparse-disk setting.** Current WSL versions disable it because it can corrupt
   data (`--set-sparse true` fails with `E_INVALIDARG`). Do **not** force it with
   `--allow-unsafe`. The disk file only grows, but E: has plenty of room. To reclaim space
   later, shut WSL down and compact the file by hand. In PowerShell as Administrator:
   ```powershell
   wsl --shutdown
   diskpart
   # inside diskpart:
   select vdisk file="E:\WSL\Ubuntu-24.04\ext4.vhdx"
   attach vdisk readonly
   compact vdisk
   detach vdisk
   exit
   ```
6. Confirm the disk file is on E:, not C:
   ```powershell
   Get-ChildItem E:\WSL\Ubuntu-24.04
   ```
   You should see `ext4.vhdx`.

### Freeing space on C: (optional)
- The `docker-desktop` WSL distro already on this machine keeps its own virtual disk on C:.
  If you don't use Docker, uninstalling Docker Desktop frees that space.
- If the existing Linux VM (VirtualBox/VMware) stores its disk on C:, it is not needed for this
  project and can be moved or deleted.
- To remove Ubuntu completely later: `wsl --unregister Ubuntu-24.04`. This deletes the
  whole Linux disk, so push to GitHub first.

### Optional: let WSL use all cores and enough RAM
By default WSL2 gets all logical cores and 50% of RAM. That is fine for us. If you ever need
to change it, create `C:\Users\lenovo\.wslconfig`:
```ini
[wsl2]
processors=8
memory=12GB
```
then run `wsl --shutdown` in PowerShell and reopen Ubuntu.

---

## Part 2 — How to access WSL day to day

| Task | How |
|---|---|
| Open a Linux terminal | Start menu → **Ubuntu 24.04**, or in Windows Terminal pick the Ubuntu tab, or type `wsl` in any PowerShell |
| Go to the project | `cd ~/projects/DNA-Reconstruction` |
| Browse files in Explorer | In Explorer's address bar type `\\wsl$\Ubuntu-24.04\home\soban\projects` (pin it to Quick Access). Or from the Linux terminal run `explorer.exe .` |
| Edit code in VS Code | Install the VS Code extension **"WSL"** (by Microsoft). Then in the Linux terminal: `cd ~/projects/DNA-Reconstruction && code .` VS Code reopens "connected to WSL" — its terminal, Python and git all run inside Linux. |
| Reach Windows files from Linux | Windows drive is at `/mnt/c/`. e.g. the docs folder: `/mnt/c/Users/lenovo/OneDrive/Desktop/DNA\ Computing/` |
| Shut WSL down (frees RAM) | PowerShell: `wsl --shutdown` |
| Using Claude Code on the repo | Run `claude` from the Ubuntu terminal inside the repo folder so every command runs in Linux |

Rule of thumb: **edit through VS Code (WSL mode), run everything from the Ubuntu terminal.**
Avoid editing WSL files with Windows-only tools via `\\wsl$` — they can change line endings
or file permissions.

---

## Part 3 — Install the toolchain inside Ubuntu

Run these in the Ubuntu terminal.

```bash
# System update + C++ compiler, make, git, curl
sudo apt update && sudo apt upgrade -y
sudo apt install -y build-essential git curl pkg-config python3 python3-venv python3-pip

# Rust (use rustup, NOT apt: BBS uses Rust edition 2024, which needs Rust >= 1.85;
# apt's rustc is older)
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
source "$HOME/.cargo/env"

# Verify
g++ --version        # expect 13.x
cargo --version      # expect 1.85 or newer
python3 --version    # expect 3.12.x
git --version
```

---

## Part 4 — Configure git inside WSL

WSL has its own git configuration, separate from Windows git.

```bash
git config --global user.name  "Your Name"
git config --global user.email "YOUR_ID+USERNAME@users.noreply.github.com"
git config --global init.defaultBranch main
git config --global core.autocrlf input     # keep Linux line endings in the repo
git config --global pull.rebase false
```

**About the email:** the repo is public, so every commit's email is public. Use your GitHub
*noreply* address (GitHub → Settings → Emails → tick "Keep my email addresses private"; the
address shown there looks like `12345678+username@users.noreply.github.com`).

---

## Part 5 — Connect WSL to GitHub

Pick **one** of the two options. Option A is the fastest.

### Option A — GitHub CLI (`gh`) — recommended
```bash
sudo apt install -y gh
gh auth login
#   ? Where do you use GitHub?            GitHub.com
#   ? Preferred protocol for Git?         SSH
#   ? Generate a new SSH key?             Yes  (press Enter for no passphrase, or set one)
#   ? How would you like to authenticate? Login with a web browser
# Copy the one-time code, open the URL in your Windows browser, paste, approve.
gh auth status        # should say "Logged in to github.com"
```

### Option B — SSH key by hand
```bash
ssh-keygen -t ed25519 -C "wsl-dna-project"     # press Enter to accept defaults
cat ~/.ssh/id_ed25519.pub                       # copy the whole line
```
GitHub → Settings → **SSH and GPG keys** → New SSH key → paste → Save. Then test:
```bash
ssh -T git@github.com
# "Hi <username>! You've successfully authenticated..."
```

---

## Part 6 — The project repo (already done)

The repo is https://github.com/sobanali256/DNA-Reconstruction, at
`~/projects/DNA-Reconstruction`, with an HTTPS remote. `gh auth login` (HTTPS, "Authenticate
Git with your GitHub credentials: Yes") handles passwords. GitHub does **not** accept your
account password for `git push`; if it asks for one, run `gh auth login` again.

To get the repo on a fresh machine:
```bash
mkdir -p ~/projects && cd ~/projects
git clone https://github.com/sobanali256/DNA-Reconstruction.git
```

Keep folder names free of spaces. `make` and shell scripts break on paths like
`DNA Reconstruction`.

Check: `git remote -v` shows `origin`, and `git status -sb` shows `main...origin/main`.

### Running Claude Code inside Ubuntu

Install once, in the Ubuntu terminal:
```bash
curl -fsSL https://claude.ai/install.sh | bash
```
Open a new Ubuntu terminal (or run `source ~/.bashrc`), then start it from the repo:
```bash
cd ~/projects/DNA-Reconstruction
claude
```
It reads `CLAUDE.md` automatically, which gives it all the project context and decisions.

### Daily git loop
```bash
git status                     # what changed
git add <files>                # stage
git commit -m "message"        # commit
git push                       # upload
git pull                       # fetch changes (e.g. made from Colab)
```

---

## Part 7 — Things that must NOT go into the public repo

These will be enforced by `.gitignore`, but know why:

| Item | Reason |
|---|---|
| `external/reconstruction/` (ITR source) | ITR's license is "TBA" — we must not redistribute it. A setup script clones it at a pinned commit; only our small wrapper/patch is committed. |
| `external/bbs/` | Cloned at a pinned commit by the setup script (MIT-licensed, but no need to vendor it). |
| `data/raw/`, `data/normalized/` | Large; downloaded/regenerated by scripts. A manifest with checksums is committed instead. |
| `results/` raw runs | Large. Back up separately (e.g. Google Drive); commit only small summary tables used in the paper. |
| `.venv/`, `target/`, `*.o` | Build/environment output. |

---

## Part 8 — (Optional, backup only) Using the same repo on Colab Pro

Skip this part unless the pilot shows ITR is too slow on the laptop, or you need a second
machine to rerun cached results after a bug fix. Colab is only ever used for
**accuracy-only** runs. Its runtimes are never reported, because every runtime in the paper
must come from this laptop.

In a Colab cell:
```bash
!git clone https://github.com/sobanali256/DNA-Reconstruction.git
%cd DNA-Reconstruction
!bash scripts/setup_external.sh      # clones + builds BBS and ITR at pinned commits
```
Colab runtimes are wiped when they disconnect, so results from long runs are written to a
mounted Google Drive folder and copied back into `results/` on the laptop.

---

## Part 9 — Before any timing run on this laptop

- Plug in the charger; Windows power mode → **Best performance**.
- Close browsers/OneDrive sync activity; let the machine idle for a minute.
- Worker counts: 1, 2, 4 are within the 4 physical cores; 8 uses hyper-threads and is reported
  as such.
- The i5-10210U is a 15 W laptop chip that throttles under sustained load — this is why
  every timing condition is repeated ≥3 times and the median is reported.

---

## Quick troubleshooting

| Symptom | Fix |
|---|---|
| `wsl` opens docker-desktop | `wsl --set-default Ubuntu-24.04` |
| `Failed to attach disk ... ERROR_SHARING_VIOLATION` | Windows has `ext4.vhdx` attached as a drive. It shows as a 1 TB **RAW** disk; **never format or initialize it**. Admin PowerShell: `Dismount-DiskImage -ImagePath "E:\WSL\Ubuntu-24.04\ext4.vhdx"`, then `wsl`. Or: Disk Management → right-click that disk → Detach VHD. Never double-click `ext4.vhdx` in File Explorer. |
| `cargo: command not found` | `source ~/.cargo/env` (or open a new terminal) |
| `Permission denied (publickey)` on push | Re-run Part 5; check `ssh -T git@github.com` |
| VS Code terminal shows PowerShell, not bash | You opened the folder from Windows. Close it and run `code .` from the Ubuntu terminal |
| Everything is very slow | You are working under `/mnt/c/...`. Move to `~/projects/...` |
| WSL uses too much RAM | `wsl --shutdown`, or set `memory=` in `.wslconfig` |
| Want to see how big the Linux disk is | PowerShell: `Get-Item E:\WSL\Ubuntu-24.04\ext4.vhdx \| Select Length`; inside Linux: `df -h /` |
| Linux disk grew after deleting files | Compact it with `diskpart` (Part 1, step 5). Never use `--set-sparse --allow-unsafe` |
