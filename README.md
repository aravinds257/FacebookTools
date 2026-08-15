# Facebook Marketplace PC Scraper (Powered by Google Gemini)

Automated Facebook Marketplace scraper and PC deal appraisal tool powered by **Playwright** and **Google Gemini API (`gemini-2.5-flash`)**.

---

## 1. Get a Free Google Gemini API Key
1. Go to [Google AI Studio](https://aistudio.google.com/)
2. Click **Create API key**
3. Copy your key (it's **100% free** under Google's free tier).

---

## 2. Local Setup & Running (macOS)

### Install Dependencies
```bash
cd /Users/aravinds257/Work/FacebookTools

# Install Python packages
pip install playwright google-genai

# Install Playwright Chromium browser
playwright install chromium
```

### Run Locally
```bash
# Set your free Gemini API key
export GEMINI_API_KEY="your-gemini-api-key-here"

# Run the scraper
python3 marketplaceScrapper.py
```

---

## 3. Deploying & Scheduling on Oracle Cloud VM

### Transfer Files to Oracle VM
```bash
# 1. Ensure private key permissions are secure
chmod 600 /Users/aravinds257/Downloads/Oracle/ssh-key-2026-08-06.key

# 2. Transfer marketplaceScrapper.py to the Oracle VM
scp -i /Users/aravinds257/Downloads/Oracle/ssh-key-2026-08-06.key \
    /Users/aravinds257/Work/FacebookTools/marketplaceScrapper.py \
    opc@132.226.131.154:~/marketplaceScrapper.py
```

### SSH into Oracle VM
```bash
ssh -i /Users/aravinds257/Downloads/Oracle/ssh-key-2026-08-06.key opc@132.226.131.154
```

### VM Setup (Oracle Linux / RHEL)
```bash
# 1. Update and install python3
sudo dnf update -y && sudo dnf install -y python3 python3-pip

# 2. Create & activate virtual environment
python3 -m venv ~/scraper_env
source ~/scraper_env/bin/activate

# 3. Install packages
pip install --upgrade pip
pip install playwright google-genai
playwright install chromium

# 4. Install Chromium OS dependencies on Oracle Linux (if needed)
sudo dnf install -y alsa-lib atk cups-libs gtk3 libXcomposite libXcursor libXdamage libXext libXi libXrandr libXScrnSaver libXtst pango xorg-x11-fonts-100dpi xorg-x11-fonts-75dpi xorg-x11-utils xorg-x11-fonts-cyrillic xorg-x11-fonts-Type1 xorg-x11-font-utils mesa-libgbm nss nspr

# 5. Test manual run
export GEMINI_API_KEY="your-gemini-api-key-here"
python3 ~/marketplaceScrapper.py
```

### Schedule 24/7 Run via Crontab on Oracle VM
```bash
crontab -e
```
Add this line to run every 3 hours:
```cron
0 */3 * * * export GEMINI_API_KEY="your-gemini-api-key-here" && /home/opc/scraper_env/bin/python3 /home/opc/marketplaceScrapper.py >> /home/opc/scraper.log 2>&1
```
