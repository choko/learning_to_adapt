import keras.backend as K
from keras.engine.topology import Layer

class LDA(Layer):

  def __init__(self, feat_dim=40, kernel_size=5, **kwargs):
    super(LDA, self).__init__(**kwargs)

    self.feat_dim = feat_dim
    self.kernel_size = kernel_size

  def build(self, input_shape):
    self.lda = self.add_weight(
        shape=(self.kernel_size, self.feat_dim, self.feat_dim * self.kernel_size),
        initializer="ones",
        name="lda",
        trainable=self.trainable,
        regularizer=None,
        constraint=None)

    self.bias = self.add_weight(
        shape=(self.feat_dim * self.kernel_size,),
        initializer="zeros",
        name="bias",
        trainable=self.trainable,
        regularizer=None,
        constraint=None)

  def call(self, x):
    shape = K.shape(x)
    if K.ndim(x) == 4:
      x = K.reshape(x, (-1, shape[-2], shape[-1]))
      x = K.conv1d(x, self.lda, data_format="channels_last") + self.bias
      return K.reshape(x, (shape[0], shape[1], shape[2] - self.kernel_size + 1, self.feat_dim * self.kernel_size))
    elif K.ndim(x) == 5:
      x = K.reshape(x, (-1, shape[-2], shape[-1]))
      x = K.conv1d(x, self.lda, data_format="channels_last") + self.bias
      return K.reshape(x, (shape[0], shape[1], shape[2], shape[3] - self.kernel_size + 1, self.feat_dim * self.kernel_size))

  def compute_output_shape(self, input_shape):
    return input_shape[:-1] + (self.feat_dim * self.kernel_size,)


class LHUC(Layer):
  """
  Straightforward LHUC just adding a scalar with no activation after a layer.
  """

  def build(self, input_shape):
    self.r = self.add_weight(
        shape=(input_shape[-1],),
        initializer="ones",
        name="lhuc_weights",
        trainable=self.trainable,
        regularizer=None, constraint=None)

  def call(self, x):
    return x * self.r

  def compute_output_shape(self, input_shape):
    return input_shape

class SparseLHUC(Layer):

  def __init__(self, beta=2./3., gamma=-0.1, delta=1.1, regularizer=None, **kwargs):
    super(SparseLHUC, self).__init__(**kwargs)

    self.beta = beta
    self.gamma = gamma
    self.delta = delta
    self.regularizer = regularizer

  def build(self, input_shape):
    self.loga = self.add_weight(
        shape=(input_shape[-1],),
        initializer="zeros",
        name="loga",
        trainable=self.trainable,
        regularizer=self.regularizer,
        constraint=None)

  def call(self, x):
    return K.in_train_phase(self.call_training(self.loga, x), self.call_inference(self.loga, x))

  def call_training(self, loga, x):
    u = K.random_uniform(K.shape(loga))
    s = K.sigmoid((K.log(u) - K.log(1 - u) + loga) / self.beta)
    return self._scale(s) * x

  def call_inference(self, loga, x):
    s = K.sigmoid(loga)
    return self._scale(s) * x

  def _scale(self, s):
    return K.minimum(1., K.maximum(0., s * (self.delta - self.gamma) + self.gamma))

  def compute_output_shape(self, input_shape):
    return input_shape


class Renorm(Layer):

  def call(self, x):
    dim = K.cast(K.shape(x)[-1], K.floatx())
    return K.l2_normalize(x, axis=-1) * K.sqrt(dim)

  def compute_output_shape(self, input_shape):
    return input_shape


class FeatureTransform(Layer):

  def build(self, input_shape):
    self.rescale = self.add_weight(
      shape=(input_shape[-1],),
      initializer="ones",
      name="rescale",
      trainable=self.trainable)

    self.shift = self.add_weight(
      shape=(input_shape[-1],),
      initializer="zeros",
      name="shift",
      trainable=self.trainable)

  def call(self, x):
    return x * self.rescale + self.shift

  def compute_output_shape(self, input_shape):
    return input_shape


class Multiply(Layer):

  def call(self, inputs):
    return inputs[0] * inputs[1]

  def compute_output_shape(self, input_shapes):
    return input_shapes[0]


class SparseMultiply(Layer):

  def __init__(self, beta=2./3., gamma=-0.1, delta=1.1, **kwargs):
    super(SparseMultiply, self).__init__(**kwargs)

    self.beta = beta
    self.gamma = gamma
    self.delta = delta

  def call(self, inputs):
    x = inputs[0]
    loga = inputs[1]

    return K.in_train_phase(self.call_training(loga, x), self.call_inference(loga, x))

  def call_training(self, loga, x):
    u = K.random_uniform(K.shape(loga))
    s = K.sigmoid((K.log(u) - K.log(1 - u) + loga) / self.beta)
    return self._scale(s) * x

  def call_inference(self, loga, x):
    s = K.sigmoid(loga)
    return self._scale(s) * x

  def _scale(self, s):
    return K.minimum(1., K.maximum(0., s * (self.delta - self.gamma) + self.gamma))

  def compute_output_shape(self, input_shapes):
    return input_shapes[0]


class SDBatchNormalization(Layer):

  def __init__(self, num_speakers=9572, momentum=0.99, epsilon=1e-3, **kwargs):
    super(SDBatchNormalization, self).__init__(**kwargs)

    self.num_speakers = num_speakers
    self.momentum = momentum
    self.epsilon = epsilon
    self.axis = -1

  def build(self, input_shapes):
    dim = input_shapes[0][-1]
    shape = (self.num_speakers, dim)

    self.gamma = self.add_weight(
      shape=shape,
      name='gamma',
      initializer='ones')
    self.beta = self.add_weight(
      shape=shape,
      name='beta',
      initializer='zeros')
    self.moving_mean = self.add_weight(
      shape=(dim,),
      name='moving_mean',
      initializer='zeros',
      trainable=False)
    self.moving_variance = self.add_weight(
      shape=(dim,),
      name='moving_variance',
      initializer='ones',
      trainable=False)

    self.built = True

  def call(self, inputs, training=None):
    inputs, spk_id = inputs
    spk_id = K.cast(K.flatten(spk_id)[0], 'int32')

    def normalize_inference():
      return K.normalize_batch_in_training(inputs, self.gamma[spk_id], self.beta[spk_id], [0, 1], epsilon=self.epsilon)[0]

    normed_training, mean, variance = K.normalize_batch_in_training(
      inputs, self.gamma[spk_id], self.beta[spk_id], [0, 1], epsilon=self.epsilon)

    sample_size = K.shape(inputs)[1]
    sample_size = K.cast(sample_size, dtype=K.dtype(inputs))
    variance *= sample_size / (sample_size - (1.0 + self.epsilon))

    self.add_update([
      K.moving_average_update(self.moving_mean, mean, self.momentum),
      K.moving_average_update(self.moving_variance, variance, self.momentum)
    ], inputs)

    # Pick the normalized form corresponding to the training phase.
    return K.in_train_phase(normed_training, normalize_inference, training=training)

  def compute_output_shape(self, input_shapes):
    return input_shapes[0]

  def get_config(self):
    base_config = super(SDBatchNormalization, self).get_config()
    config = {
      'num_speakers': self.num_speakers,
      'momentum': self.momentum,
      'epsilon': self.epsilon,
    }

    return dict(list(base_config.items()) + list(config.items()))


class UttBatchNormalization(Layer):

  def __init__(self, epsilon=1e-3, **kwargs):
    super(UttBatchNormalization, self).__init__(**kwargs)

    self.epsilon = epsilon
    self.axis = -1

  def build(self, input_shapes):
    dim = input_shapes[-1]
    shape = (dim,)

    self.gamma = self.add_weight(
      shape=shape,
      name='gamma',
      initializer='ones')
    self.beta = self.add_weight(
      shape=shape,
      name='beta',
      initializer='zeros')

    self.built = True

  def call(self, inputs, training=None):
    return K.normalize_batch_in_training(inputs, self.gamma, self.beta, [0, 1], epsilon=self.epsilon)[0]

  def compute_output_shape(self, input_shapes):
    return input_shapes

  def get_config(self):
    base_config = super(UttBatchNormalization, self).get_config()
    config = {
      'epsilon': self.epsilon,
    }

    return dict(list(base_config.items()) + list(config.items()))
