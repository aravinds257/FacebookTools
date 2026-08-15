# Scraper Setup & Automation Guide

This guide walks you through setting up your API keys and automating the scraper using a 4-hour `cron` schedule on both your local Mac and your Oracle Cloud VM.

## 🔑 Prerequisites: API Keys
Before starting on either machine, ensure you have your three keys ready from the previous steps:
1. `GEMINI_API_KEY` (from Google AI Studio)
2. `PUSHOVER_USER` (from Pushover Dashboard)
3. `PUSHOVER_TOKEN` (from Pushover Application)

---

## 💻 Part 1: Setting up on your Local Mac

Your Mac will use `marketplaceScrapper.py` (which checks both Facebook and Gumtree).

### 1. Add your Environment Variables
Macs use `zsh` as the default terminal shell.

1. Open your Mac terminal and run:
   ```bash
   nano ~/.zshrc
   ```
2. Paste the following at the bottom of the file (replace with your actual keys):
   ```bash
   export GEMINI_API_KEY="your_gemini_key"
   export PUSHOVER_USER="your_pushover_user_key"
   export PUSHOVER_TOKEN="your_pushover_app_token"
   ```
3. Save (`Ctrl+O`, `Enter`, `Ctrl+X`) and reload the shell:
   ```bash
   source ~/.zshrc
   ```

### 2. Schedule the Background Job (Cron)
1. In your Mac terminal, open the cron editor:
   ```bash
   crontab -e
   ```
2. Press `i` to enter insert mode, and paste the following line:
   ```bash
   0 */4 * * * source ~/.zshrc && /Users/aravinds257/Work/.venv/bin/python3 /Users/aravinds257/Work/FacebookTools/marketplaceScrapper.py >> /Users/aravinds257/Work/FacebookTools/scraper_log.txt 2>&1
   ```
3. Press `Esc`, type `:wq`, and hit `Enter` to save and exit.
*(Your Mac will now run the scraper every 4 hours, provided it is awake and connected to the internet).*

---

## ☁️ Part 2: Setting up on your Oracle VM

Your Oracle VM will use `oracleVMscrapper.py` (which strictly scrapes Gumtree to bypass Facebook's data center IP blocks).

### 1. Transfer the Updated Script to the VM
From your Mac terminal, push the latest script over to the VM:
```bash
scp -i /Users/aravinds257/Downloads/Oracle/ssh-key-2026-08-06.key \
    /Users/aravinds257/Work/FacebookTools/oracleVMscrapper.py \
    ubuntu@<YOUR_VM_IP>:~/
```

### 2. Add your Environment Variables
Ubuntu Linux uses `bash` as the default terminal shell.

1. SSH into your Oracle VM:
   ```bash
   ssh -i /Users/aravinds257/Downloads/Oracle/ssh-key-2026-08-06.key ubuntu@<YOUR_VM_IP>
   ```
2. Open the bash configuration file:
   ```bash
   nano ~/.bashrc
   ```
3. Paste the following at the bottom (replace with your actual keys):
   ```bash
   export GEMINI_API_KEY="your_gemini_key"
   export PUSHOVER_USER="your_pushover_user_key"
   export PUSHOVER_TOKEN="your_pushover_app_token"
   ```
4. Save (`Ctrl+O`, `Enter`, `Ctrl+X`) and reload the shell:
   ```bash
   source ~/.bashrc
   ```

### 3. Schedule the Background Job (Cron)
1. On your Oracle VM terminal, open the cron editor:
   ```bash
   crontab -e
   ```
2. Scroll to the bottom and paste the following line:
   ```bash
   0 */4 * * * source ~/.bashrc && /home/ubuntu/scraper_env/bin/python3 /home/ubuntu/oracleVMscrapper.py >> /home/ubuntu/scraper_log.txt 2>&1
   ```
   > [!NOTE]
   > *If your python virtual environment on the VM is named something different than `scraper_env`, update the python path accordingly (or just use `/usr/bin/python3` if you installed Playwright globally).*
3. Save (`Ctrl+O`, `Enter`, `Ctrl+X` if using nano).

*(Your VM will now run autonomously 24/7, checking Gumtree every 4 hours and pinging your phone when new deals drop!)*
