import os
import json
import requests
import datetime
import asyncio
from threading import Thread
from time import sleep

from telegram import Update, InputFile
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters
from github import Github, GithubException

# Configuration
TELEGRAM_TOKEN = "7491481953:AAGAVTze-of67v4ZtafgVeBy5WlYEpKZG4M"
TOKEN_API = "http://jwt-3.vercel.app/token?uid={uid}&password={password}"
AUTO_UPDATE_INTERVAL = 1 * 60 * 60  # 6 hours in seconds

# User data storage
user_data = {}

class User:
    def __init__(self, user_id):
        self.user_id = user_id
        self.github_token = None
        self.repository = None
        self.target_file = None
        self.guest_accounts = []
        self.generated_tokens = []
        self.setup_step = 0  # 0=not started, 1=token, 2=repo, 3=file, 4=accounts
        self.auto_update_task = None
        self.auto_update_active = False
        self.last_update_time = None

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        user_data[user_id] = User(user_id)
    user = user_data[user_id]
    
    auto_status = ""
    if user.auto_update_active:
        auto_status = "Active"
        if user.last_update_time:
            next_run = user.last_update_time + datetime.timedelta(seconds=AUTO_UPDATE_INTERVAL)
            auto_status += f"\n⏳ Next auto-update: {next_run.strftime('%Y-%m-%d %H:%M:%S')}"
    else:
        auto_status = "Inactive"
    
    await update.message.reply_text(
        "👋 Welcome to the Token Manager Bot!\n\n"
        "🔹 Use /newuser to set up your account\n"
        "🔹 Use /token to generate tokens\n"
        "🔹 Use /updatetoken to update GitHub\n"
        "🔹 Use /delete to remove your data\n"
        "🔹 Use /run to start auto-updates\n"
        f"🔄 Auto-update status: {auto_status}\n\n"
        "For more commands, use /help"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = """
📚 Available Commands:

🔹 /start - Start the bot and see your status
🔹 /newuser - Set up your account (must be done first)
🔹 /token - Generate tokens from guest accounts
🔹 /updatetoken - Update tokens on GitHub
🔹 /delete - Remove all your data from the bot
🔹 /run - Start auto-updates (every 6 hours)

📝 How it works:
1. First setup with /newuser
2. Generate tokens with /token
3. Update GitHub with /updatetoken
4. Use /run to start auto-updates every 6 hours
"""
    await update.message.reply_text(help_text)

async def newuser_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if update.message.chat.type != "private":
        await update.message.reply_text("⚠️ Please use this command in private messages (DMs).")
        return
    user_data[user_id] = User(user_id)
    user = user_data[user_id]
    user.setup_step = 1
    await update.message.reply_text(
        "🆕 New user setup started!\n\n"
        "1. Please send your GitHub personal access token:"
    )

async def handle_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        return
    user = user_data[user_id]
    text = update.message.text.strip() if update.message and update.message.text else ""
    if user.setup_step == 1:
        user.github_token = text
        user.setup_step = 2
        await update.message.reply_text(
            "✅ GitHub token saved!\n\n"
            "2. Now send your repository name in format: owner/repo\n"
            "Example: dggaming/99999"
        )
    elif user.setup_step == 2:
        if '/' not in text:
            await update.message.reply_text("⚠️ Invalid format. Please use: owner/repo")
            return
        user.repository = text
        user.setup_step = 3
        await update.message.reply_text(
            "✅ Repository saved!\n\n"
            "3. Now send the target JSON filename (must end with .json)\n"
            "Example: token_ind.json"
        )
    elif user.setup_step == 3:
        if not text.lower().endswith('.json'):
            await update.message.reply_text("⚠️ File must end with .json")
            return
        user.target_file = text
        user.setup_step = 4
        await update.message.reply_text(
            "✅ Target file saved!\n\n"
            "4. Now UPLOAD your guest accounts .json file in this EXACT format:\n\n"
            "[\n"
            '    {\n'
            '        "uid": "3745752307",\n'
            '        "password": "YOUR_PASSWORD_HASH"\n'
            '    }\n'
            "]"
        )

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        return
    user = user_data[user_id]
    if user.setup_step != 4:
        return
    document = update.message.document
    if not document.file_name.endswith('.json'):
        await update.message.reply_text("⚠️ File must be a .json file!")
        return
    file = await context.bot.get_file(document.file_id)
    file_data = await file.download_as_bytearray()
    try:
        accounts = json.loads(file_data.decode('utf-8'))
        if not isinstance(accounts, list):
            raise ValueError("Must be an array of accounts")
        for account in accounts:
            if not isinstance(account, dict):
                raise ValueError("Each account must be an object")
            if "uid" not in account or "password" not in account:
                raise ValueError("Each account must have uid and password")
            if not isinstance(account["uid"], str) or not isinstance(account["password"], str):
                raise ValueError("UID and password must be strings")
        user.guest_accounts = accounts
        user.setup_step = 0
        
        await update.message.reply_text(
            "✅ Guest accounts validated and saved!\n\n"
            "Setup complete! You can now:\n"
            "• Generate tokens with /token\n"
            "• Update GitHub with /updatetoken\n"
            "• Start auto-updates with /run"
        )
    except Exception as e:
        await update.message.reply_text(
            f"⚠️ Invalid file format. Error: {str(e)}\n\n"
            "Please upload a .json file in EXACTLY this format:\n\n"
            "[\n"
            '    {\n'
            '        "uid": "3745752307",\n'
            '        "password": "YOUR_PASSWORD_HASH"\n'
            '    }\n'
            "]"
        )

async def token_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        await update.message.reply_text("⚠️ Please use /newuser first to set up your account.")
        return
    user = user_data[user_id]
    if not user.guest_accounts:
        await update.message.reply_text("⚠️ No guest accounts found. Please complete setup with /newuser.")
        return
    try:
        await update.message.reply_text("🔑 Generating tokens from guest accounts...")
        user.generated_tokens = []
        failed_accounts = []
        
        for account in user.guest_accounts:
            uid = account["uid"]
            password = account["password"]
            
            try:
                response = requests.get(TOKEN_API.format(uid=uid, password=password))
                if response.status_code == 200:
                    new_token = response.json().get("token", "")
                    if new_token:
                        user.generated_tokens.append(new_token)
                        await update.message.reply_text(f"✅ Token generated for UID: {uid}")
                    else:
                        await update.message.reply_text(f"⚠️ Empty token received for UID: {uid}")
                        failed_accounts.append(uid)
                else:
                    await update.message.reply_text(f"❌ Failed for UID: {uid} (Status: {response.status_code})")
                    failed_accounts.append(uid)
            except Exception as e:
                await update.message.reply_text(f"⚠️ Error processing UID {uid}: {str(e)}")
                failed_accounts.append(uid)
        
        if user.generated_tokens:
            success_msg = f"🎉 Successfully generated {len(user.generated_tokens)} tokens!\nUse /updatetoken to update them on GitHub."
            if failed_accounts:
                success_msg += f"\n\n⚠️ Failed to generate tokens for {len(failed_accounts)} accounts: {', '.join(failed_accounts)}"
            await update.message.reply_text(success_msg)
        else:
            await update.message.reply_text("❌ Failed to generate tokens for all accounts. Please check your guest accounts and try again.")
    except Exception as e:
        await update.message.reply_text(f"❌ Critical error during token generation: {str(e)}")

async def update_token_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        await update.message.reply_text("⚠️ Please use /newuser first to set up your account.")
        return
    user = user_data[user_id]
    if not user.generated_tokens:
        await update.message.reply_text("⚠️ No tokens generated yet. Use /token first.")
        return
    if not user.github_token:
        await update.message.reply_text("⚠️ Missing GitHub token. Please set up with /newuser.")
        return
    if not user.repository or not user.target_file:
        await update.message.reply_text("⚠️ Incomplete setup. Please complete with /newuser.")
        return
    try:
        await update.message.reply_text("🔄 Attempting to update tokens on GitHub...")
        g = Github(user.github_token)
        repo = g.get_repo(user.repository)
        token_data = [{"token": token} for token in user.generated_tokens]
        try:
            file_content = repo.get_contents(user.target_file)
            repo.update_file(
                user.target_file,
                "Updated tokens via bot",
                json.dumps(token_data, indent=2),
                file_content.sha
            )
            action = "updated"
        except:
            repo.create_file(
                user.target_file,
                "Created tokens via bot",
                json.dumps(token_data, indent=2)
            )
            action = "created"
            
        user.last_update_time = datetime.datetime.now()
        await update.message.reply_text(
            f"✅ {len(user.generated_tokens)} tokens successfully {action} in {user.target_file}!\n"
            f"Repository: {user.repository}\n"
            "Stored in exact format:\n"
            "[\n"
            '  {"token": "..."},\n'
            '  {"token": "..."}\n'
            "]"
        )
        
        user.generated_tokens = []
    except GithubException as ge:
        await update.message.reply_text(
            f"❌ GitHub API Error:\nStatus: {ge.status}\nMessage: {str(ge)}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ Critical error during GitHub update: {str(e)}")

async def delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if update.message.chat.type != "private":
        await update.message.reply_text("⚠️ Please use this command in private messages (DMs).")
        return
    if user_id in user_data:
        user = user_data[user_id]
        if user.auto_update_task:
            user.auto_update_task.cancel()
        del user_data[user_id]
        await update.message.reply_text(
            "🗑️ All your data has been deleted.\nYou can start fresh with /newuser if needed."
        )
    else:
        await update.message.reply_text("ℹ️ No user data found to delete.")

async def run_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_data:
        await update.message.reply_text("⚠️ Please use /newuser first to set up your account.")
        return
        
    user = user_data[user_id]
    
    if user.auto_update_active:
        await update.message.reply_text("ℹ️ Auto-updates are already running.")
        return
        
    if not all([user.github_token, user.repository, user.target_file, user.guest_accounts]):
        await update.message.reply_text("⚠️ Setup incomplete. Please complete all steps with /newuser first.")
        return
        
    user.auto_update_active = True
    user.auto_update_task = asyncio.create_task(auto_update_tokens(user_id))
    await update.message.reply_text(
        "🔄 Auto-updates started!\n"
        "Tokens will be generated and updated every 6 hours.\n"
    )

async def auto_update_tokens(user_id):
    user = user_data.get(user_id)
    if not user:
        return
        
    while user.auto_update_active:
        try:
            # Generate tokens
            user.generated_tokens = []
            failed_accounts = []
            
            for account in user.guest_accounts:
                uid = account["uid"]
                password = account["password"]
                
                try:
                    response = requests.get(TOKEN_API.format(uid=uid, password=password))
                    if response.status_code == 200:
                        new_token = response.json().get("token", "")
                        if new_token:
                            user.generated_tokens.append(new_token)
                except Exception:
                    failed_accounts.append(uid)
            
            if user.generated_tokens:
                # Update GitHub
                try:
                    g = Github(user.github_token)
                    repo = g.get_repo(user.repository)
                    token_data = [{"token": token} for token in user.generated_tokens]
                    
                    try:
                        file_content = repo.get_contents(user.target_file)
                        repo.update_file(
                            user.target_file,
                            "Auto-updated tokens via bot",
                            json.dumps(token_data, indent=2),
                            file_content.sha
                        )
                    except:
                        repo.create_file(
                            user.target_file,
                            "Auto-created tokens via bot",
                            json.dumps(token_data, indent=2)
                        )
                    
                    user.last_update_time = datetime.datetime.now()
                    next_run = user.last_update_time + datetime.timedelta(seconds=AUTO_UPDATE_INTERVAL)
                    
                    success_msg = f"✅ Auto-update completed! {len(user.generated_tokens)} tokens updated on GitHub.\n⏳ Next auto-update: {next_run.strftime('%Y-%m-%d %H:%M:%S')}"
                    if failed_accounts:
                        success_msg += f"\n\n⚠️ Failed to generate tokens for {len(failed_accounts)} accounts: {', '.join(failed_accounts)}"
                    
                    await application.bot.send_message(
                        chat_id=user_id,
                        text=success_msg
                    )
                except Exception as e:
                    await application.bot.send_message(
                        chat_id=user_id,
                        text=f"⚠️ Auto-update failed: {str(e)}\nWill retry in 6 hours."
                    )
            else:
                await application.bot.send_message(
                    chat_id=user_id,
                    text="❌ Auto-update failed: Couldn't generate any tokens. Will retry in 6 hours."
                )
            
            # Wait for next update cycle
            await asyncio.sleep(AUTO_UPDATE_INTERVAL)
            
        except asyncio.CancelledError:
            break
        except Exception as e:
            print(f"Error in auto-update task for user {user_id}: {str(e)}")
            await asyncio.sleep(60)  # Wait a minute before retrying

def main():
    global application
    application = Application.builder().token(TELEGRAM_TOKEN).build()
    
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("newuser", newuser_command))
    application.add_handler(CommandHandler("token", token_command))
    application.add_handler(CommandHandler("updatetoken", update_token_command))
    application.add_handler(CommandHandler("delete", delete_command))
    application.add_handler(CommandHandler("run", run_command))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_messages))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    print("✅ Bot is running...")
    application.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
