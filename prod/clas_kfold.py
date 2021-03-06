from set_seed import random_ctl
seed = random_ctl()

from fastai.text import *
from fastai.callbacks import SaveModelCallback
from fastai.layers import LabelSmoothingCrossEntropy

import sentencepiece as spm #https://github.com/google/sentencepiece
import fire

from sp_tok import *
from bin_metrics import Fbeta_binary
from sklearn.model_selection import KFold

def split_rebal_data_by_idx(all_texts_df:DataFrame, train_idx, valid_idx,
                            clas_col:str='is_humor'):
    ## rebalance cases
    print('Number of positive samples:', (all_texts_df.loc[:,clas_col] == 1).sum())
    print('Number of negative samples:',  (all_texts_df.loc[:,clas_col] == 0).sum())
    print('Total samples:', len(all_texts_df))

    df_train_all = all_texts_df.iloc[train_idx,:]
    df_valid     = all_texts_df.iloc[valid_idx,:]

    print('Valid prevalence(n = %d):'%len(df_valid),df_valid.loc[:,clas_col].sum()/ len(df_valid))
    print('Train all prevalence(n = %d):'%len(df_train_all), df_train_all.loc[:,clas_col].sum()/ len(df_train_all))
    print('all samples (n = %d)'%len(all_texts_df))
    assert len(all_texts_df) == (len(df_valid)+len(df_train_all)),'math didnt work'

    ## assumes that negative is less frequent case.  Generalize?
    rows_pos = df_train_all.loc[:,clas_col] == 1
    df_train_pos = df_train_all.loc[rows_pos]
    df_train_neg = df_train_all.loc[~rows_pos]
    print(f'Train Pos Cases:{df_train_pos.shape},Train Neg Cases:{df_train_neg.shape}')
    df_resample_pos = df_train_pos.sample(n=len(df_train_neg),replace=True,
                                          axis=0,random_state=np.random.get_state()[1][0]).reset_index(drop=True)
    df_train = pd.concat([df_resample_pos,df_train_neg],axis=0) #randomized again in DataBunch?
    print('Train prevalence (n = %d):'%len(df_train), df_train.loc[:,clas_col].sum()/ len(df_train))

    return df_train, df_valid
        
    
def fit_clas(model_path:str, sp_model:str, flat_loss:bool=True, qrnn:bool=True,
             n_hid:int=2304, load_enc:str=None, split_seed:int=None, backward:bool=False,
             wd:float=0.):
    PATH = Path(model_path)
    # torch.backends.cudnn.enabled=False
    
    defaults.text_spec_tok.append(NL) #add a New Line special char
    sp_vocab = Vocab( get_itos(sp_model) )
    mycust_tok = CustomTokenizer(SPTokenizer, sp_model, pre_rules=default_rules)
    
    all_texts_df = pd.read_csv('../data/haha_2019_train.csv')
    raw_text = all_texts_df.loc[:,'text']

    print("Default Rules:\n",[x.__name__ for x in default_rules],"\n\n")
    for rule in default_rules: raw_text = raw_text.apply(lambda x: rule(str(x)))
    all_texts_df['new_text'] = raw_text #databunch adds `xxbos` so don't add here
    
    kfolder = KFold(n_splits=5, random_state=split_seed, shuffle=True)
    for n_fold, (train_idx,valid_idx) in enumerate(kfolder.split(all_texts_df)):
        print(f'Kfold: {n_fold} of 5')
        df_train,df_valid = split_rebal_data_by_idx(all_texts_df,train_idx,valid_idx,clas_col='is_humor')    
        data = TextClasDataBunch.from_df(PATH,df_train,df_valid,
                                       tokenizer=mycust_tok, vocab=sp_vocab,
                                         text_cols='new_text', label_cols='is_humor', backwards=backward)
        config = awd_lstm_clas_config.copy()
        config['qrnn'] = qrnn
        config['n_hid'] = n_hid
        print(config)
        learn = text_classifier_learner(data, AWD_LSTM, drop_mult=0.7,pretrained=False,config=config)
        learn.metrics += [Fbeta_binary(beta2=1,clas=1)]
        if load_enc : learn.load_encoder(load_enc)
        if flat_loss: learn.loss_func = FlattenedLoss(LabelSmoothingCrossEntropy)
        learn.fit_one_cycle(2, 1e-2, wd=wd )
        learn.unfreeze()
        learn.fit_one_cycle(15, slice(1e-3/(2.6**4),5e-3), moms=(0.7,0.4), wd=wd, pct_start=0.25, div_factor=8.,
                            callbacks=[SaveModelCallback(learn,every='improvement',mode='max',
                                                         monitor='fbeta_binary',name=f'best_acc_model_Q_{seed}')])
        learn.save(f"haha_clas_0609_fld{n_fold}_{seed}{'_bwd' if backward else ''}")
        df_metrics = pd.DataFrame(np.array(learn.recorder.metrics),columns=learn.recorder.metrics_names)
        print(f"Clas Fold: {n_fold} RndSeed: {seed},{df_metrics['accuracy'].max()},{df_metrics['fbeta_binary'].max()}")
    
if __name__ == "__main__":
    fire.Fire(fit_clas)
