#coding: UTF-8
import os
import slack
import boto3
from logging import getLogger,StreamHandler,Formatter,DEBUG,INFO,WARNING,ERROR,CRITICAL
import itertools
import traceback
from datetime import datetime
from collections import defaultdict

logger = getLogger(__name__)
logger.setLevel(DEBUG)
loghandler = StreamHandler()
loghandler.setFormatter(Formatter("%(asctime)s %(name)s %(levelname)8s %(message)s"))
logger.addHandler(loghandler)

logger.info('処理を開始します')

client = slack.WebClient(token=os.environ['SLACK_API_TOKEN'])
bot_client = slack.WebClient(token=os.environ['SLACK_BOT_API_TOKEN'])

post_channel = os.environ['POST_CHANNEL']
post_channel_id = os.environ['POST_CHANNEL_ID']

logger.info('環境変数を設定しました')

def handler(event, lambda_context): 

    try:
        #実行中のゲームがない場合は早期リターン
        if not is_game_timeout():
            message_nogame = '実行中のゲームがありません'
            logger.info(message_nogame)
            return

        ##最新のgame_idを取得
        current_game_id = get_game_id()
        logger.debug('current_game_id:'+str(current_game_id))

        #しりとりの結果リストを取得 #しりとり0件の場合は空のリストが返る
        progress_list = get_progress(current_game_id)

        #優勝者を取得
        winner = None
        winner_id = get_winner(current_game_id)
        if winner_id:
            logger.debug('winner_id:'+winner_id)
            user_profile = client.users_profile_get(user=winner_id) #users.proflieの権限が必要
            logger.debug('user_proflie:'+str(user_profile))
        
            if not user_profile.get('ok',None):
                logger.error('ユーザー名の取得に失敗しました')
                logger.error('処理を中止します')
                exit(1)
        
            winner = user_profile['profile']['display_name']
            if not winner: #display_nameが未登録の場合はreal_nameを使う
                winner = user_profile['profile']['real_name']               
            
        logger.info(f'winner_name:{winner}')
        
        #gameテーブルを更新(現在のゲームを終了にする)
        update_game_table(current_game_id)

        #Slackに結果を投稿
        ret = send_result_to_slack(progress_list,winner)
        if not ret:
            logger.error('slack投稿に失敗しました')
            logger.error('処理を中止します')
            exit(1)

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
    
    
def get_words(game_id):
    """
    wordテーブルのデータを取得   
    引数  :  game_id    int         ゲームID
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


def get_progress(game_id):
    """
    しりとりの結果リストを返す
    引数　  :game_id       int  ゲームID
    戻り値  :progress_list list 結果リスト
    """
    logger.info('Function get_progress')
    logger.info('game_id:'+str(game_id))

    words = get_words(game_id)
    sorted_words = sorted(words, key=lambda x:x['id'])
    progress_list = []

    for w in sorted_words:
        if w.get('isValid',None) and w.get('poster',None):
            progress_list.append(w.get('word'))

    logger.debug(','.join(progress_list))

    return progress_list


def get_winner(game_id):
    """
    しりとり成功回数がもっとも多いユーザーを返す
    引数　   : game_id    int    ゲームID
    戻り値   : winner    str   勝利者(しりとり成功回数がもっとも多いユーザー)
    """

    logger.info('Function get_winner')
    logger.info('game_id:'+str(game_id))
    
    words = get_words(game_id)
    valid_words = []

    logger.debug('words:'+str(words))

    for a in reversed(words):
        if a.get('isValid',None) and a.get('poster',None):
            valid_words.append(a)

    logger.debug('valid_words:'+str(valid_words))

    if not len(valid_words):
        logger.warning('しりとりが成功した履歴が1件もありませんでした')
        logger.info('winner:None')
        return None

    words_by_poster_dict = defaultdict(int)

    for v in valid_words:
        poster = v['poster']
        words_by_poster_dict[poster] += 1

    logger.info(str(words_by_poster_dict))
    winner = None
    max_cnt = 0

    for k,v in words_by_poster_dict.items():
        logger.info(f'ユーザーごとのリスト:{k}:{v}')
        if v > max_cnt:
            max_cnt = v
            winner = k

    logger.info('winner:'+str(winner))

    return winner


def send_result_to_slack(progress_list,winner):
    """
    結果をSlackに投稿する
    引数　   : progress_list list     経過
              winner       str      勝利者(しりとり成功回数がもっとも多いユーザー)
    戻り値   : result       boolean  処理結果
    """
    logger.info('Function send_result_to_slack')

    if progress_list:
        logger.info('progress_list:'+','.join(progress_list))
    else:
        logger.info('progress_list:None')

    if winner:
        logger.info('winner:'+str(winner))
    else:
        logger.info('winner:None')

    try:
        logger.debug('try文')

        if winner:
            line1start = '今回のしりとりは、'
            delimiter = '->'
            line1end = 'だったよ！'

            line1 = line1start + delimiter.join(progress_list) + line1end
            logger.debug(line1)
            bot_client.chat_postMessage(channel=post_channel, text=line1)

            line2start = '優勝者は...'
            line2end = '、おめでとう'
            line2 = line2start + winner + line2end
            logger.debug(line2)
            bot_client.chat_postMessage(channel=post_channel, text=line2)

            line3 = 'また参加してねー'
            logger.debug(line3)
            bot_client.chat_postMessage(channel=post_channel, text=line3)

        else:
            msg = 'しりとり成功した人は誰もいなかったよ。また参加してねー'
            logger.debug(msg)
            bot_client.chat_postMessage(channel=post_channel, text=msg)

    except Exception as e:
        logger.error(e)
        return False
    
    return True


def update_game_table(game_id):
    """
    ゲームテーブルの実行中フラグを更新する
    引数　   : game_id    int        ゲームID
    戻り値   : return    boolean    処理結果
    """

    logger.info('Function update_game_table')
    logger.info('game_id:'+str(game_id))

    dynamodb = boto3.resource('dynamodb')
    game_table = dynamodb.Table('game')

    try:    
        response = game_table.update_item(
            Key={
                'id': game_id
            },
            UpdateExpression="set isEnded=:e",
            ExpressionAttributeValues={
                ':e': True
            },
            ReturnValues="ALL_NEW"
        )

        logger.debug('gameテーブル更新結果:'+str(response))

    except Exception as e:
        logger.error('DB接続でエラーが発生しました')
        raise e

    return True


def is_game_timeout():
    """
    終了処理の対象ゲームがあるか調べる
    
    引数    :  なし
    戻り値  :  isGame boolean 終了処理対象のゲームがある(True)か、ない(False)か。
    """

    logger.info('Function is_game_timeout')

    dynamodb = boto3.resource('dynamodb')
    game_table = dynamodb.Table('game')

    try:
        res = game_table.scan()
        logger.info(res)

    except Exception as e:
        logger.warning(f'gameテーブルへの接続に失敗しました:{e}')
        raise e

    for item in res.get('Items'):
        logger.debug(item.get('isEnded'))
        logger.debug(str(type(item.get('isEnded'))))
        if not item.get('isEnded'):
            logger.debug(f'実行中のゲームがあります:game_id:{item["id"]}')

            end_time_str = item.get('endDate') + ' ' + item.get('endTime')
            logger.debug('ゲーム終了時刻:'+end_time_str)
            logger.debug('現在時刻:'+str(datetime.now()))

            if datetime.strptime(end_time_str,'%Y/%m/%d %H:%M:%S') < datetime.now():
                logger.info(f'終了するゲームがあります:game_id:{item["id"]}')
                return True

    logger.info('終了するゲームはありません')
    return False



    



    