#coding: UTF-8
import os
import json
import sys
import urllib.parse
import boto3
from boto3.dynamodb.conditions import Key
from logging import getLogger,StreamHandler,Formatter,DEBUG,INFO,WARNING,ERROR,CRITICAL
import decimal
from datetime import datetime
import math
import slack
import traceback


logger = getLogger(__name__)
logger.setLevel(DEBUG)
loghandler = StreamHandler()
loghandler.setFormatter(Formatter("%(asctime)s %(name)s %(levelname)8s %(message)s"))
logger.addHandler(loghandler)

logger.info('処理を開始します')

AWS_S3_BUCKET_NAME = os.environ['PUT_BACKET']
client = slack.WebClient(token=os.environ['SLACK_API_TOKEN'])
bot_client = slack.WebClient(token=os.environ['SLACK_BOT_API_TOKEN'])
post_channel = os.environ['POST_CHANNEL']
s3 = boto3.client('s3')
rekognition = boto3.client('rekognition')
TWO_DAYS = 180000 # 若干余裕を持っている

logger.info('環境変数を設定しました')

def handler(event, context):
    logger.info("Received event: " + json.dumps(event))

    #実行中のゲームがない場合は早期リターン
    if not is_game_in_progress():
        message_nogame = '実行中のゲームがありません！'
        logger.warning(message_nogame)
        logger.info('処理を中断します')
        exit(1)

    # Get the object from the event and show its content type
    bucket = event['Records'][0]['s3']['bucket']['name']
    key = urllib.parse.unquote_plus(event['Records'][0]['s3']['object']['key'], encoding='utf-8')

    logger.debug(f'S3バケット名:{bucket}')
    logger.debug(f'S3に投稿されたファイル名:{key}')

    try:

        ##Rekognitionに投げてAI判定結果の単語を取得
        retrekog = rekognition.detect_labels(
                Image={
                    'S3Object': {
                    'Bucket': AWS_S3_BUCKET_NAME,
                    'Name': key,
                }
            },
            MaxLabels=10
        )
        
        logger.debug('retrekog:'+str(retrekog))
        
        
        ##最新のgame_idを取得
        current_game_id = get_game_id()
        logger.debug('current_game_id:'+str(current_game_id))
        
        ##最後の文字を取得
        prev_next_char = get_next_char(current_game_id)
        logger.debug('prev_next_char:'+prev_next_char)
        
        ##AI判定した単語に、しりとり成立する単語があるか探す
        next_word = get_next_word(retrekog,prev_next_char,current_game_id)
        logger.debug('next_word:'+str(next_word))
        
        ##Dynamodbに投げるJson作成
        prev_id = get_valid_word_id(current_game_id)
        word_id = get_word_id(current_game_id)+1

        dt = datetime.now()
        current_time = math.floor(dt.timestamp())
        
        image_id = int(key[:-4]) #ファイル名から拡張子.pngを除く
        is_valid = bool(next_word)
        
        if is_valid:
            ##しりとり成立の場合
            ####next_charを設定
            next_char = next_word[-1].upper()
            ####wordをしりとり成立した値に更新
            word = next_word
        
        else:
            ##しりとり不成立の場合
            ####next_charは空文字
            next_char = ''
            ####wordを最も一致度が高い単語に更新
            word = most_confident_word(retrekog)
            
        put_json = {
                    'id':word_id,
                    'gameId':int(current_game_id),
                    'isValid':is_valid,
                    'word':word,
                    'nextChar':next_char,
                    'postTime':int(current_time),
                    'poster':get_poster(image_id),
                    'prevId':int(prev_id),
                    'retJson':retrekog,
                    'unixTime':int(current_time)+TWO_DAYS,
        }

        logger.debug('put_json:'+str(put_json))
            
        ##Dynamodbのwordテーブルにデータ挿入
        ret_db = insert_word_table(put_json)
        logger.debug('データ挿入の戻り値:'+str(ret_db))
        
        if ret_db:
            logger.info('DynamoDBにデータを挿入しました')
        else:
            logger.error('DynamoDBへのデータ挿入に失敗しました')
            sys.exit(1)
        
        ##結果をslackに投稿
        ret_slack = send_message_to_slack(put_json,prev_next_char)
        
        if ret_slack:
            logger.info('Slackへの投稿に成功しました')
        else:
            logger.error('Slackへの投稿に失敗しました')
            sys.exit(1)
        
        logger.info('処理を終了します.')

    except Exception as e:
        message_error = f'エラーが発生しました:{traceback.format_exc()}'
        logger.error('message_error')
        bot_client.chat_postMessage(channel=post_channel, text=message_error)
        logger.error('処理を中止します')
        exit(1)


def get_game_id():
    """
    最新のgame_idを取得
    
    引数    :  なし
    戻り値  :  game_id    int  最新のgame_id
    """
    
    logger.info('Function get_game_id')
    
    dynamodb = boto3.resource('dynamodb')
    game_table = dynamodb.Table('game')
    game_res = game_table.scan()

    game_id = 0
    for item in game_res.get('Items'):
        if game_id < item.get('id'):
                game_id = item.get('id')
    return game_id
    
    
def get_word_id(game_id):
    """
    最新のword_idを取得(isValid=falseも含む)
    
    引数  :  game_id    int  最新のgame_id
    戻り値:  word_id    int  現時点での最後のword_id
    """
    
    logger.info('Function get_word_id')
    logger.info('game_id:'+str(game_id))
    
    word_res = get_words(game_id)
    
    logger.debug('word_res:'+str(word_res))
    
    word_id = 0
    
    for item in word_res:
            if word_id < item.get('id'):
                word_id = item.get('id')
                
    return word_id


def get_valid_word_id(game_id):
    """
    最新のword_idを取得
    
    引数  :  game_id    int  最新のgame_id
    戻り値:  word_id    int  現時点での最後のword_id
    """
    
    logger.info('Function get_valid_word_id')
    logger.info('game_id:'+str(game_id))
    
    word_res = get_words(game_id)
    
    logger.debug('word_res:'+str(word_res))
    
    word_id = 0
    
    for item in word_res:
        if item.get('isValid'):
            if word_id < item.get('id'):
                word_id = item.get('id')
                
    return word_id
    
    
def get_words(game_id):
    """
    wordテーブルのデータを取得   ##FIX 検索はqueryで行いたい
    引数  :  game_id    int        game_id
    戻り値:  words     list<dict>  wordテーブルのデータ
    """
    logger.info('Function get_words')
    logger.info('game_id:'+str(game_id))
    
    dynamodb = boto3.resource('dynamodb')
    word_table = dynamodb.Table('word')
    word_res = word_table.scan()

    logger.debug('word_res:'+str(word_res))
    
    words = []
    for i in word_res.get('Items'):
        if i.get('gameId') == game_id:
            words.append(i)

    return words

    
def get_next_char(game_id):
    """
    現時点での最後の文字を取得
    引数  :  game_id    int  最新のgame_id
    戻り値:  next_char  str  しりとり成立する文字
    """
    
    logger.info('Function get_next_char')
    logger.info('game_id:'+str(game_id))
    
    words = get_words(game_id)
    
    logger.debug('words:'+str(words))
    
    max_word_id = 0
    next_char = ''
    
    for wr in words:
        if wr.get('gameId') == game_id and max_word_id < wr.get('id') and wr.get('isValid'):
            max_word_id = wr.get('id')
            next_char = wr.get('nextChar').upper()
            
    return next_char


def get_next_word(retrekog,next_char,game_id):
    """
    しりとり成立する単語を返す
    
    引数:  retrekog dict  最新のgame_id
           next_char str   しりとり成立する頭文字
    戻り値:next_word str しりとり成立した単語。なければNone
    """
    
    logger.info('Function get_next_word')
    logger.info('retrekog:'+str(retrekog))
    logger.info('nextChar:'+str(next_char))
    logger.info('game_id:'+str(game_id))
    
    labels = retrekog.get('Labels')
    
    words = get_words(game_id)
    past_words = []
    for w in words:
        logger.debug(f'これまでの単語:{str(w)}')
        if w.get('isValid'):
            past_words.append(w.get('word'))
        
    logger.info('これまでの単語:'+','.join(past_words))

    next_word = None
    duplicate = False
    
    for label in labels:
        candidate = label.get('Name')
        if candidate[0] == next_char:
            for pw in past_words:
                if candidate == pw:
                    logger.info(f'すでにこの単語は出ています:{candidate}')
                    msg = f'{candidate}はもう出ているよ！'
                    bot_client.chat_postMessage(channel=post_channel, text=msg)
                    duplicate = True
                    break

            if not duplicate:#過去に出ていない単語の場合
                next_word = candidate
                break
    
    logger.info('次の単語:'+str(next_word))

    return next_word
    
    
def insert_word_table(put_json):
    """
    Dynamodbのwordテーブルにデータを挿入する
    
    引数:   put_json  dict    挿入するデータ
    戻り値: ret      boolean データ挿入の成否
    """
    
    logger.info('Function insert_word_table')
    logger.info('json:'+str(put_json))
    
    dynamodb = boto3.resource('dynamodb')
    word_table = dynamodb.Table('word')
    
    item = dict_float_to_decimal(put_json)
    
    try:
        res = word_table.put_item(Item=item)
        logger.info(res)

    except Exception as e:
        logger.error(e)
        return False
    
    return True


def dict_float_to_decimal(json_obj):
    """
    dictのfloat型要素をdecimal型に変更する
    
    引数:   json_obj dict 変換前dict
    戻り値: json_obj dict 変換後dict
    """
    
    logger.info('Function dict_float_to_decimal')
    logger.info('json:'+str(json_obj))
    
    for key,value in json_obj.items():
        
        if isinstance(value,dict):
            json_obj[key] = dict_float_to_decimal(value)
        elif isinstance(value,list):
            json_obj[key] = list_float_to_decimal(value)
        elif isinstance(value,float):
            json_obj[key] = decimal.Decimal(value)
        else:
            pass
        
    return json_obj


def list_float_to_decimal(list_obj):
    """
    listのfloat型要素をdecimal型に変更する
    
    引数:   list_obj list 変換前list
    戻り値: list_obj list 変換後list
    """
    
    logger.info('Function list_float_to_decimal')
    logger.info('list:'+str(list_obj))
    
    for index,value in enumerate(list_obj):
        
        if isinstance(value,dict):
            list_obj[index] = dict_float_to_decimal(value)
        elif isinstance(value,list):
            list_obj[index] = list_float_to_decimal(value)
        elif isinstance(value,float):
            list_obj[index] = decimal.Decimal(value)
        else:
            pass
    
    return list_obj
    

def send_message_to_slack(result_json,prev_next_char):
    """
    判定結果をSlackに投稿する
    
    引数:   result_json    dict     判定結果json
            prev_next_char  str      1個前の単語の最後の文字
    戻り値: ret           boolean  Slack投稿成否
    """
    
    logger.info('Function send_message_to_slack')
    logger.info('result_json:'+str(result_json))
    logger.info('prev_next_char:'+prev_next_char)
    
    ok_msg = f"Good！ それは{result_json.get('word')}だね。次の文字は{result_json.get('nextChar','')}だよ！"
    ng_msg = f"残念！ それは{result_json.get('word')}だね。{prev_next_char}で始まるものを描いてね！"
    ng_msg_unknown = f"ごめんね！ 何の絵か分からなかったよ。{prev_next_char}で始まるものを描いてね！"
    ng_msg_dup = f"残念！ それは{result_json.get('word')}だね。もう出ているよ！"
    
    if result_json.get('isValid'):
        msg = ok_msg
    elif result_json.get('word',None):
        if result_json['word'][0] == prev_next_char:
            msg = ng_msg_dup
        else:
            msg = ng_msg
    else:
        msg = ng_msg_unknown

        
    logger.debug(msg)
    
    try:
        logger.debug('try文')
        bot_client.chat_postMessage(channel=post_channel, text=msg)
    except Exception as e:
        logger.error(e)
        return False
    
    return True
    
    
def most_confident_word(retrekog):
    """
    rekognitionの戻り値を引数に取って、最もconfidentの値が高い単語を返す
    ただし、rekognitionの判定結果(Labels)が空だった場合はNoneを返す

    引数:   retrekog            dict     rekognitionの戻り値
    戻り値: most_confident_word   str      最もconfidentの値が高い単語
    """
    
    max_confidence = 0.0
    most_confident_word = None
    
    for word in retrekog.get('Labels'):
        if max_confidence < word.get('Confidence'):
            max_confidence = word.get('Confidence')
            most_confident_word = word.get('Name')
    
    return most_confident_word
    
    
def get_poster(image_id):
    """
    画像のIDから、投稿者を返す
    
    引数:   image_id    int    画像ID
    戻り値: poster     str    投稿者名 テーブルに存在しない場合は、'defalut_user'を返す
    """

    logger.info('Function get_poster')
    logger.info('image_id:'+str(image_id))

    dynamodb = boto3.resource('dynamodb')
    image_table = dynamodb.Table('image')
 
    try:
        res = image_table.get_item(Key={'imageId': image_id})
        logger.info(res)
        poster = res.get('Item').get('poster','default_user')

    except Exception as e:
        logger.warning(f'imageテーブルからユーザーを取得できませんでした:{e}')
        return 'default_user'

    return poster


def is_game_in_progress():
    """
    実行中のゲームがあるか調べる
    
    引数    :  なし
    戻り値  :  isGame boolean 実行中のゲームがある(True)か、ない(False)か。
    """

    logger.info('Function is_game_in_progress')

    dynamodb = boto3.resource('dynamodb')
    game_table = dynamodb.Table('game')

    try:
        res = game_table.scan()
        logger.info(res)

    except Exception as e:
        logger.warning(f'gameテーブルへの接続に失敗しました:{e}')
        raise e

    for item in res.get('Items'):
        if not item.get('isEnded'):
            logger.debug('実行中のゲームがあります')
            return True

    logger.debug('実行中のゲームはありません')
    return False