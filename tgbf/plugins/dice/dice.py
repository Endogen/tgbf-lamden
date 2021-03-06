import logging
import tgbf.emoji as emo

from telegram import Update, ParseMode
from telegram.ext import CommandHandler, CallbackContext
from telegram.utils.helpers import escape_markdown as esc_mk
from tgbf.lamden.connect import Connect
from tgbf.plugin import TGBFPlugin


class Dice(TGBFPlugin):

    def load(self):
        if not self.table_exists("bets"):
            sql = self.get_resource("create_bets.sql")
            self.execute_sql(sql)

        self.add_handler(CommandHandler(
            self.name,
            self.dice_callback,
            run_async=True))

    @TGBFPlugin.blacklist
    @TGBFPlugin.send_typing
    def dice_callback(self, update: Update, context: CallbackContext):
        if len(context.args) != 2:
            min = self.config.get("min_amount")
            max = self.config.get("max_amount")

            update.message.reply_text(
                self.get_usage({"{{min}}": min, "{{max}}": max}),
                parse_mode=ParseMode.MARKDOWN)
            return

        amount = context.args[0]
        number = context.args[1]

        min_amount = self.config.get("min_amount")
        max_amount = self.config.get("max_amount")

        try:
            amount = float(amount)
            if amount < min_amount or amount > max_amount:
                raise ValueError()
        except:
            # Validate amount of TAU to bet
            msg = f"{emo.ERROR} Amount not valid. " \
                  f"Provide a value between {min_amount} and {max_amount} TAU (first argument)"
            update.message.reply_text(msg)
            return

        # Convert to Integer if possible
        if not amount.is_integer():
            msg = f"{emo.ERROR} Amount (first argument) needs to be a whole number"
            update.message.reply_text(msg)
            return

        amount = int(amount)

        try:
            # Validate dice number
            number = int(number)
            if number < 1 or number > 6:
                raise ValueError()
        except:
            # Validate number of points to bet on
            msg = f"{emo.ERROR} Number of points not valid. " \
                  f"Provide a whole number between 1 and 6 (as second argument)"
            update.message.reply_text(msg)
            return

        bet_msg = esc_mk(f"You bet {amount} TAU to roll a {number}", version=2)
        send_msg = f"{emo.HOURGLASS} Sending {amount} TAU to bot"
        msg = f"{bet_msg}\n\n{send_msg}"

        logging.info(f"{bet_msg} - {update}")

        contract = self.config.get("contract")
        function = self.config.get("function")

        user_id = update.effective_user.id
        user_wallet = self.get_wallet(user_id)
        user_api = Connect(user_wallet)

        balance = user_api.get_balance()
        balance = balance["value"] if "value" in balance else 0
        balance = balance if balance else 0

        extra = 1

        if float(balance) < amount + extra:
            msg = f"{emo.ERROR} You must have at least {amount + extra} TAU"
            update.message.reply_text(msg)
            return

        message = update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)

        bot_api = Connect(self.bot_wallet)

        # Return bet amount to user in case of error
        def payback(amn=amount):
            return_bet = bot_api.send(amn, user_wallet.verifying_key)
            logging.info(f"Returned {amn} TAU due to error: {return_bet}")

        try:
            # Send the bet amount to bot wallet
            send = user_api.send(amount, self.bot_wallet.verifying_key)
        except Exception as e:
            logging.error(f"Could not send transaction: {e}")
            msg = f"{bet_msg}\n\n{emo.ERROR} {esc_mk(str(e), version=2)}"
            message.edit_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
            return

        logging.info(f"Sent {amount} TAU to bot wallet: {send}")

        if "error" in send:
            logging.error(f"Error in sending TAU: {send['error']}")
            msg = f"{bet_msg}\n\n{emo.ERROR} {esc_mk(send['error'], version=2)}"
            message.edit_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
            return

        # Get transaction hash
        tx_hash = send["hash"]

        # Wait for transaction to be completed
        success, result = user_api.tx_succeeded(tx_hash)

        if not success:
            logging.error(f"Transaction not successful: {result}")
            msg = f"{bet_msg}\n\n{emo.ERROR} {esc_mk(result, version=2)}"
            message.edit_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
            return

        ex_url = f"{user_api.explorer_url}/transactions/{tx_hash}"
        send_msg = f"{emo.DONE} [Sending {amount} TAU to bot]({ex_url})"

        dice_msg = f"{emo.HOURGLASS} Calling dice contract"

        msg = f"{bet_msg}\n\n{send_msg}\n{dice_msg}"
        message.edit_text(msg, parse_mode=ParseMode.MARKDOWN_V2)

        bck_msg = f"Paying back {amount} TAU due to error"

        try:
            # Roll the dice - execute smart contract
            roll = user_api.post_transaction(5, contract, function, {})
        except Exception as e:
            logging.error(f"Could not send transaction: {e}")
            msg = f"{bet_msg}\n\n{send_msg}\n{emo.ERROR} {esc_mk(str(e), version=2)}"
            message.edit_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
            return

        logging.info(f"Dice rolled: {roll}")

        if "error" in roll:
            logging.error(f"Error in rolling dice: {roll['error']}")

            payback()

            msg = f"{bet_msg}\n\n{send_msg}\n{emo.ERROR} {esc_mk(roll['error'] + '. ' + bck_msg, version=2)}"
            message.edit_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
            return

        if "hash" in roll:
            # Get transaction hash for rolling the dice
            roll_hash = roll["hash"]
        else:
            msg = f"No 'hash' in transaction result"
            logging.error(msg)
            self.notify(msg)

            msg = f"{bet_msg}\n\n{send_msg}\n{emo.ERROR} {esc_mk(msg, version=2)}"
            message.edit_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
            return

        # Wait for transaction to be completed
        success, result = user_api.tx_succeeded(roll_hash)

        if success:
            try:
                int(result["result"])
            except:
                msg = f"Wrong dice result"
                logging.error(f"{msg}: {result}")
                self.notify(f"{msg}: {result}")

                payback()

                msg = f"{bet_msg}\n\n{send_msg}\n{emo.ERROR} {bck_msg}"
                update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
                return
        else:
            msg = "Error on rolling dice"
            logging.error(f"{msg}: {result}")
            self.notify(f"{msg}: {result}")

            payback()

            msg = f"{bet_msg}\n\n{send_msg}\n{emo.ERROR} {bck_msg}"
            update.message.reply_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
            return

        ex_url = f"{user_api.explorer_url}/transactions/{roll_hash}"
        dice_msg = f"{emo.DONE} [Calling dice contract]({ex_url})"

        msg = f"{bet_msg}\n\n{send_msg}\n{dice_msg}"
        message.edit_text(msg, parse_mode=ParseMode.MARKDOWN_V2)

        amount_back = 0
        won_tx_hash = str()

        # Determine if user won or not
        if int(result["result"]) == int(number):
            # --- USER WON ---
            multiplier = self.config.get("multiplier")
            amount_back = amount * multiplier

            result_msg = f"You rolled a {result['result']} and WON!! {emo.MONEY_FACE}"
            return_msg = f"{emo.HOURGLASS} Sending {amount_back} TAU to user"

            msg = f"{bet_msg}\n\n{send_msg}\n{dice_msg}\n\n{esc_mk(result_msg, version=2)}\n\n{return_msg}"
            message.edit_text(msg, parse_mode=ParseMode.MARKDOWN_V2)

            try:
                # Return amount of TAU to user
                send_won = bot_api.send(amount_back, user_wallet.verifying_key)
            except Exception as e:
                msg = f"Could not sent won amount to user: {e}"
                logging.error(msg)
                self.notify(msg)

                msg = f"{bet_msg}\n\n{send_msg}\n{dice_msg}\n\n{esc_mk(result_msg, version=2)}\n\n{emo.ERROR} {esc_mk(str(e), version=2)}"
                message.edit_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
                return

            logging.info(f"Sent {amount_back} TAU back to user: {send_won}")

            if "error" in send_won:
                msg = f"Error sending won amount to user: {send_won['error']}"
                logging.error(msg)
                self.notify(msg)

                msg = f"{bet_msg}\n\n{send_msg}\n{dice_msg}\n\n{esc_mk(result_msg, version=2)}\n\n{emo.ERROR} {esc_mk(send_won['error'], version=2)}"
                message.edit_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
                return

            if "hash" in send_won:
                # Get transaction hash
                won_tx_hash = send_won["hash"]
            else:
                msg = f"No 'hash' in transaction result"
                logging.error(msg)
                self.notify(msg)

                msg = f"{bet_msg}\n\n{send_msg}\n{dice_msg}\n\n{esc_mk(result_msg, version=2)}\n\n{emo.ERROR} {esc_mk(msg, version=2)}"
                message.edit_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
                return

            # Wait for transaction to be completed
            success, res = user_api.tx_succeeded(won_tx_hash)

            if not success:
                msg = f"Sending amount to user not successful: {res}"
                logging.error(msg)
                self.notify(msg)

                msg = f"{bet_msg}\n\n{send_msg}\n{dice_msg}\n\n{esc_mk(result_msg, version=2)}\n\n{emo.ERROR} {esc_mk(res, version=2)}"
                message.edit_text(msg, parse_mode=ParseMode.MARKDOWN_V2)
                return

            ex_url = f"{user_api.explorer_url}/transactions/{won_tx_hash}"
            return_msg = f"{emo.DONE} [Sending {amount_back} TAU to user]({ex_url})"

            msg = f"{bet_msg}\n\n{send_msg}\n{dice_msg}\n\n{esc_mk(result_msg, version=2)}\n\n{return_msg}"
            message.edit_text(msg, parse_mode=ParseMode.MARKDOWN_V2)

        else:
            # --- USER LOST ---
            result_msg = f"You rolled a {result['result']} and lost {emo.SAD}"
            msg = f"{bet_msg}\n\n{send_msg}\n{dice_msg}\n\n{esc_mk(result_msg, version=2)}"
            message.edit_text(msg, parse_mode=ParseMode.MARKDOWN_V2)

        # Insert details into database
        self.execute_sql(
            self.get_resource("insert_bet.sql"),
            user_id,
            amount,
            number,
            tx_hash,
            result["result"],
            amount_back,
            won_tx_hash)
