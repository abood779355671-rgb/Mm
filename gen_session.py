"""
سكربت توليد SESSION_STRING لحساب مستخدم (userbot) باستخدام Kurigram.
شغّله مرة واحدة لكل حساب مساعد تريد إضافته، ثم اجمع كل القيم الناتجة
مفصولة بفاصلة في متغير SESSION_STRINGS بملف .env (أو استخدم SESSION_STRING
المفرد إذا كان لديك حساب واحد فقط).
"""
from pyrogram import Client

API_ID = int(input("API_ID: ").strip())
API_HASH = input("API_HASH: ").strip()

with Client("session_gen", api_id=API_ID, api_hash=API_HASH, in_memory=True) as app:
    s = app.export_session_string()
    print("\n=== SESSION_STRING ===")
    print(s)
    print("======================\n")
    print("احفظه في .env: كقيمة SESSION_STRING (حساب واحد)")
    print("أو أضفه إلى SESSION_STRINGS مفصولاً بفاصلة عن باقي الحسابات.")
