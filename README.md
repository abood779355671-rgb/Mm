# 🎵 بوت تلغرام للموسيقى — Arabic Music Bot

بوت تلغرام يشغّل الموسيقى في المكالمات الجماعية بأوامر عربية.

## التثبيت
sudo apt-get install -y ffmpeg
pip install -r requirements.txt
cp .env.example .env  # املأ القيم
python main.py

## الأوامر العربية
- تشغيل <اسم/رابط>
- ايقاف مؤقت
- استكمال
- تخطي
- ايقاف
- القائمة
- بنق

## الحصول على SESSION_STRING
python gen_session.py
