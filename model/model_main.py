import os
import os.path as op
import tensorflow as tf
import datetime
import numpy as np
from glob import glob
import pandas as pd

import settings
from config import opts
from model.model_builder import create_models
from tfrecords.tfrecord_reader import TfrecordGenerator
from utils.util_funcs import input_integer


def train_by_user_interaction():
    options = {"train_dir_name": "kitti_raw_train",
               "val_dir_name": "kitti_raw_test",
               "model_dir": "vode_model",
               "src_weights_name": "weights.h5",
               "dst_weights_name": "weights.h5",
               "initial_epoch": 0,
               "final_epoch": opts.EPOCHS}

    print("\n===== Select training options")

    print(f"Default options:")
    for key, value in options.items():
        print(f"\t{key} = {value}")
    print("\nIf you are happy with default options, please press enter")
    print("Otherwise, please press any other key")
    select = input()

    if select == "":
        print(f"You selected default options.")
    else:
        message = "Type 1 or 2 to specify dataset: 1) kitti_raw, 2) kitti_odom"
        ds_id = input_integer(message, 1, 2)
        if ds_id == 1:
            options["train_dir_name"] = "kitti_raw_train"
            options["val_dir_name"] = "kitti_raw_test"
        if ds_id == 2:
            options["train_dir_name"] = "kitti_odom_train"
            options["val_dir_name"] = "kitti_odom_test"

        print("Type model_dir: dir name under opts.DATAPATH_CKP to save or load model")
        options["model_dir"] = input()
        print("Type src_weights_name: load weights from {model_dir/src_weights_name}")
        options["src_weights_name"] = input()
        print("Type dst_weights_name: save weights to {model_dir/dst_weights_name}")
        options["dst_weights_name"] = input()

        message = "Type initial_epoch: number of epochs previously trained"
        options["initial_epoch"] = input_integer(message, 0, 10000)
        message = "Type final_epoch: number of epochs to train model upto"
        options["final_epoch"] = input_integer(message, 0, 10000)

    print("Training options:", options)
    train(**options)


class LM:
    @staticmethod
    def loss_for_loss(y_true, y_pred):
        return y_pred

    @staticmethod
    def loss_for_metric(y_true, y_pred):
        return tf.constant(0, dtype=tf.float32)

    @staticmethod
    def metric_for_loss(y_true, y_pred):
        return tf.constant(0, dtype=tf.float32)

    @staticmethod
    def metric_for_metric(y_true, y_pred):
        return y_pred


def train(train_dir_name, val_dir_name, model_dir, src_weights_name, dst_weights_name,
          initial_epoch, final_epoch):
    set_gpu_config()

    model_pred, model_train = create_models()
    model_train = try_load_weights(model_train, model_dir, src_weights_name)

    optimizer = tf.keras.optimizers.Adam(learning_rate=0.0002)
    losses = {"loss_out": LM.loss_for_loss, "metric_out": LM.loss_for_metric}
    metrics = {"loss_out": LM.metric_for_loss, "metric_out": LM.metric_for_metric}
    model_train.compile(optimizer=optimizer, loss=losses, metrics=metrics)

    dataset_train = TfrecordGenerator(op.join(opts.DATAPATH_TFR, train_dir_name), True, opts.EPOCHS).get_generator()
    dataset_val = TfrecordGenerator(op.join(opts.DATAPATH_TFR, val_dir_name), True, opts.EPOCHS).get_generator()
    callbacks = get_callbacks(model_dir)
    steps_per_epoch = count_steps(train_dir_name)
    val_steps = np.clip(count_steps(train_dir_name)/2, 0, 100).astype(np.int32)

    print(f"\n\n\n========== START TRAINING ON {model_dir} ==========\n\n\n")
    history = model_train.fit(dataset_train, epochs=final_epoch, callbacks=callbacks,
                              validation_data=dataset_val, steps_per_epoch=steps_per_epoch,
                              validation_steps=val_steps, initial_epoch=initial_epoch)

    save_model_weights(model_train, model_dir, dst_weights_name)
    if model_dir:
        dump_history(history.history, model_dir)


def set_gpu_config():
    gpus = tf.config.experimental.list_physical_devices('GPU')
    if gpus:
        try:
            # Currently, memory growth needs to be the same across GPUs
            for gpu in gpus:
                tf.config.experimental.set_memory_growth(gpu, True)
            logical_gpus = tf.config.experimental.list_logical_devices('GPU')
            print(len(gpus), "Physical GPUs,", len(logical_gpus), "Logical GPUs")
        except RuntimeError as e:
            # Memory growth must be set before GPUs have been initialized
            print(e)


def try_load_weights(model, model_dir, weights_name):
    if model_dir and weights_name:
        model_file_path = op.join(opts.DATAPATH_CKP, model_dir, weights_name)
        if op.isfile(model_file_path):
            print("===== load model weights", model_file_path)
            model.load_weights(model_file_path)
        else:
            print("===== train from scratch", model_file_path)
    return model


def save_model_weights(model, model_dir, weights_name):
    model_dir_path = op.join(opts.DATAPATH_CKP, model_dir)
    if not op.isdir(model_dir_path):
        os.makedirs(model_dir_path, exist_ok=True)
    model_file_path = op.join(opts.DATAPATH_CKP, model_dir, weights_name)
    model.save_weights(model_file_path)


def get_callbacks(model_dir):
    if model_dir:
        model_path = op.join(opts.DATAPATH_CKP, model_dir, "model-{epoch:02d}-{val_loss:.2f}.h5")
        log_dir = op.join(opts.DATAPATH_LOG, model_dir)
    else:
        nowtime = datetime.datetime.now()
        nowtime = nowtime.strftime("%m%d_%H%M%S")
        model_path = op.join(opts.DATAPATH_CKP, nowtime, "model-{epoch:02d}-{val_loss:.2f}.h5")
        log_dir = op.join(opts.DATAPATH_LOG, nowtime)

    if not op.isdir(model_path):
        os.makedirs(op.dirname(model_path), exist_ok=True)

    callbacks = [
        tf.keras.callbacks.ModelCheckpoint(
            filepath=model_path,
            monitor="val_loss",
            save_best_only=True,
            save_freq="epoch",
            save_weights_only=True
        ),
        tf.keras.callbacks.TensorBoard(
            log_dir=log_dir
        ),
    ]
    return callbacks


def count_steps(dataset_dir):
    srcpath = op.join(opts.DATAPATH_SRC, dataset_dir)
    files = glob(op.join(srcpath, "*/*.png"))
    frames = len(files)
    steps = frames // opts.BATCH_SIZE
    print(f"[count steps] frames={frames}, steps={steps}")
    return steps


def dump_history(history, model_dir):
    df = pd.DataFrame(history)
    df.to_csv(op.join(opts.DATAPATH_CKP, model_dir, "history.txt"), float_format="%.3f")
    print("save history\n", df)


def predict_by_user_interaction():
    options = {"test_dir_name": "kitti_raw_test",
               "model_dir": "vode_model",
               "weights_name": "weights.h5"
               }

    print("\n===== Select prediction options")

    print(f"Default options:")
    for key, value in options.items():
        print(f"\t{key} = {value}")
    print("\nIf you are happy with default options, please press enter")
    print("Otherwise, please press any other key")
    select = input()

    if select == "":
        print(f"You selected default options.")
    else:
        message = "Type 1 or 2 to specify dataset: 1) kitti_raw, 2) kitti_odom"
        ds_id = input_integer(message, 1, 2)
        if ds_id == 1:
            options["test_dir_name"] = "kitti_raw_test"
        if ds_id == 2:
            options["test_dir_name"] = "kitti_odom_test"

        print("Type model_dir: dir name under opts.DATAPATH_CKP and opts.DATAPATH_PRD")
        options["model_dir"] = input()
        print("Type weights_name: load weights from {model_dir/src_weights_name}")
        options["weights_name"] = input()

    print("Prediction options:", options)
    predict(**options)


def predict(test_dir_name, model_dir, weights_name):
    set_gpu_config()

    model_pred, model_train = create_models()
    model_train = try_load_weights(model_train, model_dir, weights_name)
    model_pred.compile(optimizer="sgd", loss="mean_absolute_error")

    dataset = TfrecordGenerator(op.join(opts.DATAPATH_TFR, test_dir_name)).get_generator()
    predictions = model_pred.predict(dataset)
    for pred in predictions:
        print(f"prediction shape={pred.shape}")

    pred_depth = predictions[0]
    pred_pose = predictions[-1]
    save_predictions(model_dir, pred_depth, pred_pose)


def save_predictions(model_dir, pred_depth, pred_pose):
    pred_dir_path = op.join(opts.DATAPATH_PRD, model_dir)
    os.makedirs(pred_dir_path, exist_ok=True)
    print(f"save depth in {pred_dir_path}, shape={pred_depth.shape}")
    np.save(op.join(pred_dir_path, "depth.npy"), pred_depth)
    print(f"save pose in {pred_dir_path}, shape={pred_pose.shape}")
    np.save(op.join(pred_dir_path, "pose.npy"), pred_pose)


if __name__ == "__main__":
    # train_by_user_interaction()
    predict("kitti_raw_test", "vode_model", "weights.h5")
