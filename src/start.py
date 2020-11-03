#coding: UTF-8
import os
import slack
from datetime import datetime,timedelta
from dateutil.relativedelta import relativedelta
from logging import getLogger,StreamHandler,Formatter,DEBUG,INFO,WARNING,ERROR,CRITICAL
import json
import math
import boto3
import random
import string
import traceback
import hmac
import time
import hashlib
import urllib.parse

logger = getLogger(__name__)
logger.setLevel(DEBUG)
loghandler = StreamHandler()
loghandler.setFormatter(Formatter("%(asctime)s %(name)s %(levelname)8s %(message)s"))
logger.addHandler(loghandler)

logger.info('処理を開始します')

client = slack.WebClient(token=os.environ['SLACK_API_TOKEN'])
bot_client = slack.WebClient(token=os.environ['SLACK_BOT_API_TOKEN'])
post_channel = os.environ['POST_CHANNEL']
slack_signing_secret = os.environ['SLACK_SIGNING_SECRET']
alphabet_list = ['A','B','C','D','E','F','G','H','I','J','K','L','M','N','O','P','Q','R','S','T','U','V','W','X','Y','Z'] #HACK string.ascii_uppercaseで簡単に書けるはず
TWO_DAYS = 180000 # 若干余裕を持っている

logger.info('環境変数を設定しました')

def handler(event, lambda_context):    
    try:
        logger.debug(f'event:{event}')

        #リクエストの検証
        timestamp = event['headers']['X-Slack-Request-Timestamp']
        if abs(math.floor(time.time()) - int(timestamp)) > 60 * 5:
            logger.warning('reply attackの可能性があります')
            logger.info('処理を終了します')
            return

        logger.debug(f'timestamp:{timestamp}')
        
        body = event['body']
        sig_basestring = f'v0:{timestamp}:{parse_body(body)}'

        logger.debug(f'sig_basestring:{sig_basestring}')
        logger.debug(f'slack_signing_secret:{slack_signing_secret}')
        my_signature = 'v0=' + hmac.new(slack_signing_secret.encode(),sig_basestring.encode(),hashlib.sha256).hexdigest()

        slack_signature = event['headers']['X-Slack-Signature']

        logger.debug(f'my_signature   :{my_signature}')
        logger.debug(f'slack_signature:{slack_signature}')

        if hmac.compare_digest(my_signature, slack_signature):
            logger.info('リクエスト検証 OK')
        else:
            logger.error('リクエスト検証 NG')
            logger.error('処理を中止します')
            return

        ##引数(時)で制限時間を設定する
        limit_hour_str = event['body'].get('text',None)

        if not limit_hour_str:
            #NOTE bodyに項目がない場合も、空文字の場合も、いずれもデフォルトの1時間を設定する
            limit_hour_str = 1

        ret = validate_limit_hour(limit_hour_str)
        if not ret['is_ok']:
            logger.warning(ret['ng_msg'])
            bot_client.chat_postMessage(channel=post_channel, text=ret['ng_msg'])
            logger.info('処理を終了します')
            return
        
        #実行中のゲームがある場合は、実行中であるメッセージを出す
        if is_game_in_progress():
            message_already = 'すでにゲームは実行中だよ！'
            logger.warning(message_already)
            bot_client.chat_postMessage(channel=post_channel, text=message_already)
            logger.info('処理を終了します')
            return

        #最初の文字をランダムに決定
        first_char = random.choice(alphabet_list)

        #gameテーブル登録
        current_game_id = insert_game_table(first_char,limit_hour_str)
        if current_game_id:
            logger.info(f'今回のgame_id:{current_game_id}')
        else:
            logger.error('game_idの取得に失敗しました')
            logger.error('処理を中止します')
            exit(1)            

        #wordテーブルに最初の文字を登録
        if insert_first_char_to_word_table(first_char,current_game_id):
            logger.info('wordテーブルへの登録に成功しました')
        else:
            logger.error('wordテーブルへの登録に失敗しました')
            logger.error('処理を中止します')
            exit(1)
            
        #slackに返信を投稿
        message_ok = f'AI絵しりとりを始めるよ！最初の文字は...{first_char}。'
        logger.info(message_ok)
        bot_client.chat_postMessage(channel=post_channel, text=message_ok)

        message_ok_2 = f'締め切りは今から{limit_hour_str}時間後、ゲームスタート！'
        logger.info(message_ok_2)
        bot_client.chat_postMessage(channel=post_channel, text=message_ok_2)
        
        #処理終了のログを出力
        logger.info('処理を終了します')

    except Exception as e:
        message_error = f'エラーが発生しました:{type(e)}:{e.args}:{traceback.format_exc()}'
        logger.error(message_error)
        bot_client.chat_postMessage(channel=post_channel, text=message_error)
        logger.error('処理を中止します')
        exit(1)


def get_max_game_id():
    """
    現在登録されているgame_idのうち最大のものを返す
    
    引数    :  なし
    戻り値  :  maxId    int  現在登録されているgame_idのうち最大値
    """
    
    logger.info('START Function get_max_game_id')
    
    dynamodb = boto3.resource('dynamodb')
    game_table = dynamodb.Table('game')
    game_res = game_table.scan()
    
    maxId = 0
    for item in game_res.get('Items'):
        if maxId < item.get('id'):
                maxId = item.get('id')

    logger.info(f'maxid:{maxId}')
    logger.info('END   Function get_max_game_id')  
    return maxId


def insert_game_table(first_char,limit_hour_str):
    """
    DynamoDBのgameテーブルにデータを挿入する

    引数　:    first_char     str    最初の文字
    　　   　  limit_hour_str str    制限時間(単位:時)
    戻り値:    game_id        int    採番されたgame_id。処理失敗時は0。

    """

    logger.info('START Function insert_game_table')
    logger.info(f'first_char:{first_char}')
    logger.info(f'limit_hour_str:{limit_hour_str}')

    logger.info('START CALL Function get_max_game_id')
    game_id = get_max_game_id() + 1
    logger.info('END   CALL Function get_max_game_id')

    now = datetime.now()
    end = now + timedelta(hours=int(limit_hour_str))

    begin_date = now.strftime('%Y/%m/%d')
    begin_time = now.strftime('%H:%M:%S')

    end_date = end.strftime('%Y/%m/%d')
    end_time = end.strftime('%H:%M:%S')

    current_time = math.floor(now.timestamp())

    item = {
        'id':game_id,
        'beginDate':begin_date,
        'beginTime':begin_time,
        'endDate':end_date,
        'endTime':end_time,
        'firstChar':first_char,
        'isEnded':bool(False),
        'unixTime':int(current_time)+TWO_DAYS,
    }

    logger.debug(f'item:{item}')
    dynamodb = boto3.resource('dynamodb')
    image_table = dynamodb.Table('game')
    
    try:
        res = image_table.put_item(Item=item)
        logger.debug(res)
    except Exception as e:
        logger.error(f'データ挿入に失敗しました:{type(e)}:{e.args}')
        logger.info('game_id:0')
        return 0

    logger.info(f'game_id:{game_id}')
    logger.info('END   Function insert_game_table')   
    return game_id


def insert_first_char_to_word_table(first_char,current_game_id):
    """
    最初の文字をwordテーブルに登録
    
    引数    :  first_char   str        最初の文字
               current_game_id  int        現在のgame_id
    戻り値  :   ret        boolean    処理結果
    """

    logger.info('Start Function insert_first_char_to_word_table')
    logger.info(f'first_char:{first_char}')
    logger.info(f'current_game_id:{current_game_id}')

    dt = datetime.now()
    current_time = math.floor(dt.timestamp())

    logger.info('START CALL Function get_max_word_id')
    word_id = get_max_word_id(current_game_id-1) + 1 #NOTE current_game_id-1が直近のゲームIDになる。＃TODO わかりづらいので修正したい。
    logger.info('END   CALL Function get_max_word_id')

    logger.debug(f'current_time:{current_time}')
    logger.debug(f'word_id:{word_id}')

    put_json = {
            'id':word_id,
            'gameId':current_game_id,
            'isValid':True,
            'word':'',
            'nextChar':first_char,
            'postTime':int(current_time),
            'unixTime':int(current_time)+TWO_DAYS,
        }
    
    dynamodb = boto3.resource('dynamodb')
    word_table = dynamodb.Table('word')

    logger.debug(f'put_json:{put_json}')

    try:
        res = word_table.put_item(Item=put_json)
        logger.debug(res)

    except Exception as e:
        logger.error(f'wordテーブルへの登録に失敗しました:{type(e)}:{e.args}')
        return False
    
    logger.info('End   Function insert_first_char_to_word_table')
    return True


def is_game_in_progress():
    """
    実行中のゲームがあるか調べる
    
    引数    :  なし
    戻り値  :  isGame boolean 実行中のゲームがある(True)か、ない(False)か。
    """

    logger.info('START Function is_game_in_progress')

    dynamodb = boto3.resource('dynamodb')
    game_table = dynamodb.Table('game')

    try:
        res = game_table.scan()
        logger.info(res)

    except Exception as e:
        logger.warning(f'gameテーブルへの接続に失敗しました:{type(e)}:{e.args}')
        raise e

    for item in res.get('Items'):
        if not item.get('isEnded'):
            logger.info('実行中のゲームがあります')
            return True

    logger.info('実行中のゲームはありません')
    logger.info('END   Function is_game_in_progress')

    return False


def get_words(game_id):
    """
    wordテーブルのデータを取得   
    引数  :  game_id    int         ゲームID
    戻り値:  words     list<dict>  wordテーブルのデータ
    """
    logger.info('START Function get_words')
    logger.info(f'game_id:{game_id}')
    
    dynamodb = boto3.resource('dynamodb')
    word_table = dynamodb.Table('word')
    word_res = word_table.scan()
    
    logger.debug(f'word_res:{word_res}')

    words = []
    for i in word_res.get('Items'):
        if i.get('gameId') == game_id:
            words.append(i)    

    logger.info(f'words:{words}')
    logger.info('END   Function get_words')
    return words


def get_max_word_id(game_id):
    """
    wordテーブルの最後のIDを取得   
    引数　 :  game_id        int         現在のgame_id
    戻り値 :  max_word_id    int         現在登録されている最後のword_id
    """
    logger.info('START Function get_max_word_id')
    logger.info(f'game_id:{game_id}')

    logger.info('START CALL Function get_words')
    words = get_words(game_id)
    logger.info('END   CALL Function get_words')

    max_word_id = 0
    for w in words:
        if w.get('id',None) > max_word_id:
            max_word_id = w['id']
        
    logger.info(f'max_word_id:{max_word_id}')
    logger.info('END   Function get_max_word_id')

    return max_word_id


def validate_limit_hour(limit_hour_str):
    """    
    制限時間の妥当性チェック   
    引数　 :  limit_hour_str    str         引数で受け取ったlimit_hour_str
    戻り値 :  result            dict<is_ok:boolean/ng_msg:str>    チェックOK(true),NG(false)/NGメッセージ
    """
    logger.info('START Function validate_limit_hour')
    logger.info(f'limit_hour_str:{limit_hour_str}')
    is_ok = False
    ng_msg = ''

    try:
        limit_hour = int(limit_hour_str)
        if limit_hour > 48 or limit_hour < 1:#48時間より長い制限時間はNGとする
            ng_msg = f'制限時間[{limit_hour}]は1~48(単位:時)で設定してください'
            logger.error(ng_msg)
            is_ok = False
        else:
            is_ok = True
    except ValueError as e:
        ng_msg = f'制限時間[{limit_hour_str}]が数値ではありません:{type(e)}:{e.args}'
        logger.error(ng_msg)
        is_ok = False

    result = {
        "is_ok":is_ok,
        "ng_msg":ng_msg
        }

    logger.info(f'result:{result}')
    logger.info('End Function validate_limit_hour')

    return result


def parse_body(body):
    """    
    リクエスト検証用にdict型のbodyをパースする  
    引数　 :  body        dict    リクエストbody
    戻り値 :  body_str    str     パース後のリクエストbody文字列
    """
    logger.info('START Function parse_body')
    logger.info(f'body:{body}')

    body_str = ''

    for k in body:
        body_str += '&' + urllib.parse.quote_plus(k) + '=' + urllib.parse.quote_plus(body[k])

    body_str = body_str[1:] #1字目の&は不要

    logger.info(f'body_str:{body_str}')
    logger.info('End Function parse_body')

    return body_str

