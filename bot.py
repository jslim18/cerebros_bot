import logging
from datetime import datetime
from io import BytesIO, BufferedReader

from telegram.ext import Updater, CommandHandler, RegexHandler, \
    MessageHandler, Filters, CallbackQueryHandler
from telegram.ext.dispatcher import run_async
from telegram import ParseMode, ReplyKeyboardMarkup, ReplyKeyboardHide, \
    ChatAction, ForceReply, InlineKeyboardMarkup, InlineKeyboardButton, Emoji
from telegram.utils.botan import Botan
from pony.orm import db_session, select, desc

from credentials import TOKEN, BOTAN_TOKEN
from start_bot import start_bot
from database import db

from admin import Admin
from believer import Believer
from reporter import Reporter

import json
import requests
from requests.auth import HTTPBasicAuth

import subprocess

url = "http://public.coindaddy.io:4000/api/"
headers = {'content-type': 'application/json'}
auth = HTTPBasicAuth('rpc', '1234')

# States the bot can have (maintained per chat id)
MAIN, ADD_BELIEVER, REMOVE_BELIEVER, ADD_ADMIN, REMOVE_ADMIN, PHONE_NR,\
    ACCOUNT_NR, BANK_NAME, REMARK, SEARCH, ADD_INFO, EDIT, ATTACHMENT =\
    range(13)

options = {PHONE_NR: "Phone number", ACCOUNT_NR: "Telegram ID",
           BANK_NAME: "Name of bank account owner", REMARK: "DNA",
           ATTACHMENT: "Attachment"}

# Enable reverse lookup
for k, v in list(options.items()):
    options[v] = k

_grid = [[options[ACCOUNT_NR]],
         [options[BANK_NAME]],
         [options[PHONE_NR]],
         [options[REMARK]],
         [options[ATTACHMENT]],
         ['/cancel']]

CAT_KEYBOARD = ReplyKeyboardMarkup(_grid, selective=True)
DB_NAME = 'bot.sqlite'

state = dict()

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.DEBUG)
logger = logging.getLogger(__name__)

u = Updater(TOKEN)
dp = u.dispatcher

db.bind('sqlite', DB_NAME, create_db=True)
db.generate_mapping(create_tables=True)

with db_session:
    if len(select(a for a in Admin if a.id is 10049375)) is 0:
        # Create initial admin account
        Admin(id=10049375, first_name="Jannes", super_admin=True)
    if len(select(a for a in Admin if a.id is 46348706)) is 0:
        # Create initial admin account
        Admin(id=46348706, first_name="Jackson", super_admin=True)
        # pass

botan = False
if BOTAN_TOKEN:
    botan = Botan(BOTAN_TOKEN)

help_text = "This bot keeps a database of known trustworthy bitcoin traders by recording " \
            "their phone number, bank account number and name.\n\n" \
            "<b>Usage:</b>\n" \
            "/search - Search the database for reports\n\n" \
            "Donations via BTC are welcome: 1EPu17mBM2zw4LcupURgwsAuFeKQrTa1jy"

admin_help_text = "\n\n" \
                  "<b>Admin commands:</b>\n" \
                  "/new - Add a new trusted trader\n" \
                  "/edit - Edit an existing trusted trader\n" \
                  "/delete - Delete a trusted trader\n" \
                  "/cancel - Cancel current operation"

super_admin_help_text = "\n\n" \
                        "<b>Super Admin commands:</b>\n" \
                        "/add_admin - Register a new admin\n" \
                        "/remove_admin - Remove an admin\n" \
                        "/download_database - Download complete database"


def error(bot, update, error):
    """ Simple error handler """
    logger.exception(error)


def help(bot, update):
    """ Handler for the /help command """
    from_user = update.message.from_user
    chat_id = update.message.chat_id

    with db_session:
        admin = get_admin(from_user)

    text = help_text

    if admin:
        text += admin_help_text
        if admin.super_admin:
            text += super_admin_help_text

    bot.sendMessage(chat_id,
                    text=text,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True)


def get_admin(from_user):
    admin = Admin.get(id=from_user.id)
    if admin:
        admin.first_name = from_user.first_name
        admin.last_name = from_user.last_name
        admin.username = from_user.username
    return admin


def get_reporter(from_user):
    reporter = Reporter.get(id=from_user.id)
    if reporter:
        reporter.first_name = from_user.first_name
        reporter.last_name = from_user.last_name
        reporter.username = from_user.username
    return reporter


def message_handler(bot, update):
    global state
    forward_from = update.message.forward_from
    chat_id = update.message.chat_id
    chat_state = state.get(chat_id, MAIN)
    reply = None
    reply_markup = ReplyKeyboardHide(selective=True)

    with db_session:
        if chat_state is ADD_BELIEVER and forward_from:
            reporter = get_reporter(forward_from)
            if not reporter:
                reporter = Reporter(id=forward_from.id,
                                    first_name=forward_from.first_name,
                                    last_name=forward_from.last_name,
                                    username=forward_from.username)
                track(update, 'new_reporter')

            believer = Believer(added_by=get_admin(update.message.from_user))
            believer.reported_by.add(reporter)
            track(update, 'new_report')
            db.commit()

            reply = "Created report <b>#%d</b>! Please enter trustworthy bitcoin trader " \
                    "information:" % believer.id
            reply_markup = CAT_KEYBOARD
            state[chat_id] = [ADD_INFO, believer.id]

        elif chat_state is EDIT:
            try:
                report_id = int(update.message.text.replace('#', ''))
            except ValueError:
                reply = "Not a valid report number. Try again or use " \
                        "/cancel to abort."
            else:
                believer = Believer.get(id=report_id)

                if believer:
                    reply = "%s\n\nPlease enter new " \
                            "trustworthy bitcoin trader information:" % str(believer)
                    reply_markup = CAT_KEYBOARD
                    state[chat_id] = [ADD_INFO, believer.id]
                else:
                    reply = "Could not find report number. Try again or " \
                            "use /cancel to abort."

        elif chat_state is REMOVE_BELIEVER:
            try:
                report_id = int(update.message.text.replace('#', ''))
            except ValueError:
                reply = "Not a valid report number. Try again or use " \
                        "/cancel to abort."
            else:
                believer = Believer.get(id=report_id)
                if believer:
                    believer.delete()
                    reply = "Deleted report!"
                    del state[chat_id]
                else:
                    reply = "Could not find report number. Try again or " \
                            "use /cancel to abort."
                    reply_markup = ForceReply(selective=True)

        elif chat_state is ADD_ADMIN and forward_from:
            admin = get_admin(forward_from)
            if not admin:
                Admin(id=forward_from.id,
                      first_name=forward_from.first_name,
                      last_name=forward_from.last_name,
                      username=forward_from.username)
                reply = "Successfully added admin"
            else:
                reply = "This user is already an admin"

            del state[chat_id]

        elif chat_state is REMOVE_ADMIN and forward_from:
            admin = get_admin(forward_from)
            if admin and not admin.super_admin:
                admin.delete()
                reply = "Successfully removed admin"
            else:
                reply = "This user is not an admin"

            del state[chat_id]

        elif (isinstance(chat_state, tuple) and
              chat_state[0] is SEARCH and
              update.message.text):

            issued = chat_state[1]
            if (datetime.now() - issued).seconds > 30:
                reply = "Please send your /search query within 30 seconds."
                del state[chat_id]

            else:
                text = update.message.text.replace('%', '')

                believers = select(
                    s for s in Believer if
                    text in s.phone_nr or
                    text in s.account_nr or
                    text in s.bank_name or
                    text in s.remark
                ).order_by(
                    desc(Believer.created)
                )[0:1]
                
                good_believers = [item["identifier"] for item in believers
                                  if isHoldingESCX(item['id']) ]
                if good_believers:
                    believer = believers[0]
                    reply = str(believer)
                    reporter = get_reporter(update.message.from_user)

                    kb = search_keyboard(offset=0,
                                         show_download=True,
                                         disabled_attachments=[],
                                         confirmed=reporter in believer.reported_by
                                         if reporter
                                         else False,
                                         query=text)
                    reply_markup = InlineKeyboardMarkup(kb)
                else:
                    reply = "No search results"

                del state[chat_id]
                track(update, 'search')

        elif isinstance(chat_state, list):  # Additional info
            text = update.message.text

            if chat_state[0] is ADD_INFO and len(chat_state) is 2:
                option = options[text]
                chat_state.append(option)
                state[chat_id] = chat_state

                if option != ATTACHMENT:
                    reply = "Please enter " + update.message.text
                else:
                    reply = "Please send a photo or file to attach to this " \
                            "report"

                reply_markup = ForceReply(selective=True)

            elif chat_state[0] is ADD_INFO:
                    believer = Believer.get(id=chat_state[1])
                    text = update.message.text
                    category = chat_state[2]

                    if category is PHONE_NR and text:
                        believer.phone_nr = text

                    elif category is ACCOUNT_NR and text:
                        believer.account_nr = text

                    elif category is BANK_NAME and text:
                        believer.bank_name = text

                    elif category is REMARK and text:
                        believer.remark = text

                    elif category is ATTACHMENT:
                        if update.message.photo:
                            believer.attached_file =\
                                'photo:' + update.message.photo[-1].file_id
                        elif update.message.document:
                            believer.attached_file =\
                                'document:' + update.message.document.file_id

                    chat_state.pop()  # one menu back
                    state[chat_id] = chat_state
                    reply_markup = CAT_KEYBOARD
                    reply = "Add more info or send /cancel if you're done."

    if reply:
        bot.sendMessage(chat_id, text=reply, parse_mode=ParseMode.HTML,
                        reply_to_message_id=update.message.message_id,
                        reply_markup=reply_markup)


@run_async
def track(update, event_name):
    if botan:
        botan.track(message=update.message, event_name=event_name)


def add_believer(bot, update):
    global state
    with db_session:
        admin = get_admin(update.message.from_user)
    if not admin:
        return
    state[update.message.chat_id] = ADD_BELIEVER
    bot.sendMessage(update.message.chat_id,
                    text="Forward me a message of the user that is reporting "
                         "the trustworthy bitcoin trader or use /cancel to cancel",
                    reply_to_message_id=update.message.message_id)


def remove_believer(bot, update):
    global state
    with db_session:
        admin = get_admin(update.message.from_user)
    if not admin:
        return
    state[update.message.chat_id] = REMOVE_BELIEVER
    bot.sendMessage(update.message.chat_id,
                    text="Please send the Report # of the report you wish "
                         "to remove or send /cancel to cancel",
                    reply_markup=ForceReply(selective=True),
                    reply_to_message_id=update.message.message_id)


def edit_believer(bot, update):
    global state
    with db_session:
        admin = get_admin(update.message.from_user)
    if not admin:
        return
    state[update.message.chat_id] = EDIT
    bot.sendMessage(update.message.chat_id,
                    text="Please send the Report # of the report you wish "
                         "to edit or send /cancel to cancel",
                    reply_markup=ForceReply(selective=True),
                    reply_to_message_id=update.message.message_id)


def add_admin(bot, update):
    global state
    with db_session:
        admin = get_admin(update.message.from_user)
    if not admin or not admin.super_admin:
        return
    state[update.message.chat_id] = ADD_ADMIN
    bot.sendMessage(update.message.chat_id,
                    text="Forward me a message of the user you want to add"
                         " as admin or send /cancel to cancel",
                    reply_to_message_id=update.message.message_id)


def remove_admin(bot, update):
    global state
    with db_session:
        admin = get_admin(update.message.from_user)
    if not admin or not admin.super_admin:
        return
    state[update.message.chat_id] = REMOVE_ADMIN
    bot.sendMessage(update.message.chat_id,
                    text="Forward me a message of the admin you want to remove"
                         " or send /cancel to cancel",
                    reply_to_message_id=update.message.message_id)


def cancel(bot, update):
    try:
        del state[update.message.chat_id]
    finally:
        bot.sendMessage(update.message.chat_id,
                        text="Current operation canceled",
                        reply_markup=ReplyKeyboardHide(),
                        reply_to_message_id=update.message.message_id)


def search(bot, update):
    global state
    state[update.message.chat_id] = (SEARCH, datetime.now())
    bot.sendMessage(update.message.chat_id,
                    text="Enter search query:",
                    reply_markup=ForceReply(selective=True),
                    reply_to_message_id=update.message.message_id)


@db_session
def callback_query(bot, update):
    cb = update.callback_query
    chat_id = cb.message.chat_id

    data = update.callback_query.data

    logger.info(data)

    data = data.split('%')

    action = ''
    offset = 0
    disabled_attachments = set()
    query = ''
    confirmed = False
    show_download = True

    for elem in data:
        name, *args = elem.split('=')

        if name == 'act':
            action = args[0]
        elif name == 'off':
            offset = int(args[0])
        elif name == 'noatt':
            disabled_attachments = set(int(arg) for arg in args if arg != '')
        elif name == 'qry':
            query = '='.join(args)
        elif name == 'cnf':
            confirmed = bool(int(args[0]))
        elif name == 'dl':
            show_download = bool(int(args[0]))

    reporter = get_reporter(cb.from_user)

    if action == 'old':
        new_offset = offset + 1
    elif action == 'new':
        new_offset = offset - 1
    else:
        new_offset = offset

    try:
        believers = select(
            s for s in Believer if
            query in s.phone_nr or
            query in s.account_nr or
            query in s.bank_name or
            query in s.remark
        ).order_by(
            desc(Believer.created)
        )[new_offset:new_offset + 1]

    except TypeError:
        believers = None

    else:
        offset = new_offset

    reply = None

    if action in ('old', 'new'):
        if believers:
            believer = believers[0]
            reply = str(believer)

            if not believer.attached_file:
                disabled_attachments.add(offset)

            confirmed = reporter in believer.reported_by if reporter else False

        else:
            bot.answerCallbackQuery(callback_query_id=cb.id, text="No more results")
            return

    elif action == 'confirm':
        if not believers:
            bot.answerCallbackQuery(callback_query_id=cb.id, text="Not found, please search again")
            return

        believer = believers[0]
        if not confirmed:
            if not reporter:
                reporter = Reporter(id=cb.from_user.id,
                                    first_name=cb.from_user.first_name,
                                    last_name=cb.from_user.last_name,
                                    username=cb.from_user.username)
                track(update, 'new_reporter')

            believer.reported_by.add(reporter)
            bot.answerCallbackQuery(callback_query_id=cb.id, text="You confirmed this report.")
        else:
            believer.reported_by.remove(reporter)
            bot.answerCallbackQuery(callback_query_id=cb.id, text="You removed your confirmation.")

        confirmed = not confirmed
        reply = str(believer)

    elif action == 'att':
        if not believers:
            bot.answerCallbackQuery(callback_query_id=cb.id, text="Not found, please search again")
            return

        kind, _, file_id = believers[0].attached_file.partition(':')

        if kind == 'photo':
            bot.sendPhoto(chat_id, photo=file_id,
                          reply_to_message_id=cb.message.message_id)
        elif kind == 'document':
            bot.sendDocument(chat_id, document=file_id,
                             reply_to_message_id=cb.message.message_id)

        disabled_attachments.add(offset)

    elif action == 'dl':
        bot.sendChatAction(chat_id, action=ChatAction.UPLOAD_DOCUMENT)

        with db_session:
            believers = select(s for s in Believer if
                              query in s.phone_nr or
                              query in s.account_nr or
                              query in s.bank_name or
                              query in s.remark).limit(100)

            content = "\r\n\r\n".join(str(s) for s in believers)

        file = BytesIO(content.encode())
        show_download = False

        bot.sendDocument(chat_id, document=BufferedReader(file),
                         filename='search.txt',
                         reply_to_message_id=update.callback_query.message.message_id)

    kb = search_keyboard(offset=offset, show_download=show_download,
                         disabled_attachments=disabled_attachments, confirmed=confirmed,
                         query=query)

    reply_markup = InlineKeyboardMarkup(kb)

    if reply:
        bot.editMessageText(chat_id=chat_id, message_id=cb.message.message_id, text=reply,
                            reply_markup=reply_markup, parse_mode=ParseMode.HTML)
    else:
        bot.editMessageReplyMarkup(chat_id=chat_id,
                                   message_id=update.callback_query.message.message_id,
                                   reply_markup=reply_markup)


def search_keyboard(offset, show_download, disabled_attachments, confirmed, query):
    data = list()

    data.append('dl=' + str(int(show_download)))

    data.append('noatt=' + '='.join(str(da) for da in disabled_attachments))

    data.append('cnf=' + str(int(confirmed)))

    data.append('off=' + str(int(offset)))

    data.append('qry=' + query)

    data = '%'.join(data)

    kb = [[
        InlineKeyboardButton(
            text='<< ' + Emoji.BLACK_RIGHT_POINTING_TRIANGLE,
            callback_data='act=old%' + data
        ),
        InlineKeyboardButton(
            text=(Emoji.THUMBS_UP_SIGN) if not confirmed else
            ('Unliked'),
            callback_data='act=confirm%' + data
        ),
        InlineKeyboardButton(
            text=Emoji.BLACK_LEFT_POINTING_TRIANGLE + ' >>',
            callback_data='act=new%' + data
        ),
    ], list()]

#    if offset not in disabled_attachments:
#        kb[1].append(
#            InlineKeyboardButton(
#                text=Emoji.FLOPPY_DISK + ' Attachment',
#                callback_data='act=att%' + data
#            )
#        )

#    if show_download:
#        kb[1].append(
#            InlineKeyboardButton(
#                text=Emoji.BLACK_DOWN_POINTING_DOUBLE_TRIANGLE + ' Download all',
#                callback_data='act=dl%' + data
#            )
#        )
    return kb


def download_db(bot, update):
    chat_id = update.message.chat_id
    global state
    with db_session:
        admin = get_admin(update.message.from_user)
    if not admin or not admin.super_admin:
        return
    bot.sendChatAction(chat_id, action=ChatAction.UPLOAD_DOCUMENT)
    bot.sendDocument(chat_id, document=open(DB_NAME, 'rb'),
                     filename='trustworthy.sqlite',
                     reply_to_message_id=update.message.message_id)

def isHoldingESCX(id):
   try:
      addy = readBlockstack(id)
      bal = getESCXBalance(addy)
   except:
       return False	    
   return (bal >= 10.0)
   

def getESCXBalance(address): 
   try:
      payload = {
         "method": "get_balances",
         "params": {
            "filters":[{"field": "address", "op": "==", "value": address},
                       {"field": "asset", "op": "==", "value": "ESCX"}],
            "filterop": "and"
            },
          "jsonrpc":"2.0",
          "id":0
         }
      response = requests.post(url, data=json.dumps(payload), headers=headers, auth=auth)
      json_data = response.json()
      #quantity = json_data.quantity 
      return (json_data['result'].pop()['quantity']) / 100000000
   except: 
      return 0;

def readBlockstack(id):
   try:
      p = subprocess.check_output(['blockstack','lookup',id])
      data = json.loads(p.decode('utf-8'))
      accounts = data['profile']['account']
      bitcoins = [item["identifier"] for item in accounts
                  if item['service'] == 'bitcoin']
      return bitcoins[0]
   except:
       return ""



# Add all handlers to the dispatcher and run the bot
dp.add_handler(CommandHandler('start', help))
dp.add_handler(CommandHandler('help', help))
dp.add_handler(CommandHandler('add_admin', add_admin))
dp.add_handler(CommandHandler('remove_admin', remove_admin))
dp.add_handler(CommandHandler('new', add_believer))
dp.add_handler(CommandHandler('edit', edit_believer))
dp.add_handler(CommandHandler('delete', remove_believer))
dp.add_handler(CommandHandler('search', search))
dp.add_handler(CallbackQueryHandler(callback_query))
dp.add_handler(CommandHandler('download_database', download_db))
dp.add_handler(CommandHandler('cancel', cancel))
dp.add_handler(MessageHandler([Filters.text, Filters.photo, Filters.document],
                             message_handler))
dp.addErrorHandler(error)

start_bot(u)
u.idle()
