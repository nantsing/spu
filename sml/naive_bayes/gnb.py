# Copyright 2023 Ant Group Co., Ltd.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import unittest

import jax
import jax.numpy as jnp
import spu.spu_pb2 as spu_pb2  # type: ignore
import spu.utils.simulation as spsim
from jax import jit, lax
from sklearn import datasets


class GaussianNB:
    def __init__(self, classes_ = None, var_smoothing=1e-9):
        """Gaussian Naive Bayes (GaussianNB).
        Can perform online updates to model parameters via :meth:`partial_fit`.

        Parameters
        ----------
        classes_ : ndarray of shape (n_classes,)
            class labels known to the classifier. Should be provided when classifier
            is created.

        var_smoothing : float, default=1e-9
            Portion of the largest variance of all features that is added to
            variances for calculation stability.

        Attributes
        ----------
        first :  bool
            to determine whether the classifier is called the first time.
        
        class_count_ : ndarray of shape (n_classes,)
            number of training samples observed in each class.

        class_prior_ : ndarray of shape (n_classes,)
            probability of each class.

        epsilon_ : float
            absolute additive value to variances.
            
        var_ : ndarray of shape (n_classes, n_features)
            Variance of each feature per class.

        theta_ : ndarray of shape (n_classes, n_features)
            mean of each feature per class.

        """
        self.first_ = True
        self.var_smoothing = var_smoothing
        assert classes_ != None, f"Classes of data should be provided at first!"
        self.classes_ = classes_.sort()

    def fit(self, X, y):
        """Fit Gaussian Naive Bayes according to X, y.
        
        When fit or partial fit is called, we update mean and variance for each feature.
        When predict is called, we compute the posterior log likelihood for each class and 
        predict the class with the maximun log likelihood.
        
        This attribute is seen as the first time the classifier is called, so it 
        calls _first_partial_fit which set initial attributes. If partial_fit is called,
        it firstly figure out whether the classifier is called the first time, and then 
        calls _first_partial_fit or _partial_fit respectively to updater theta_ and var_.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Training vectors, where `n_samples` is the number of samples
            and `n_features` is the number of features.

        y : array-like of shape (n_samples,)
            Target values.
            
        Returns
        -------
        self : object
            Returns the instance itself.
        """
        self.n_sample = len(y)
        self.first_ = False
        return self._first_partial_fit(X, y)

    def partial_fit(self, X, y):
        """Incremental fit on a batch of samples.
        
        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Training vectors, where `n_samples` is the number of samples and
            `n_features` is the number of features.

        y : array-like of shape (n_samples,)
            Target values.
        
        Returns
        -------
        self : object
            Returns the instance itself.
        """
        
        self.n_sample = len(y)
        if self.first_ == True:
            self.first_ = False
            return self._first_partial_fit(X, y)
        else:
            return self._partial_fit(X, y)

    def _update_mean_variance(self, n_past, mu, var, X, N_i):
        """Compute online update of Gaussian mean and variance.

        Given starting sample count, mean, and variance, a new set of
        points X, and optionally sample weights, return the updated mean and
        variance. (NB - each dimension (column) in X is treated as independent
        -- you get variance, not covariance).

        Parameters
        ----------
        n_past : int
            Number of samples represented in old mean and variance.

        mu : array-like of shape (number of Gaussians,)
            Means for Gaussians in original set.

        var : array-like of shape (number of Gaussians,)
            Variances for Gaussians in original set.

        Returns
        -------
        total_mu : array-like of shape (number of Gaussians,)
            Updated mean for each Gaussian over the combined set.

        total_var : array-like of shape (number of Gaussians,)
            Updated variance for each Gaussian over the combined set.
        """
        N = self.n_sample
        n_new = N_i
        new_mu = jnp.sum(X, axis=0) / n_new
        new_var = jnp.sum((X - new_mu) ** 2, axis=0)
        new_var = new_var - N * new_mu**2 + N_i * new_mu**2
        new_var = new_var / n_new

        n_total = n_past + n_new

        # Combine mean of old and new data.
        total_mu = jnp.where(n_new == 0.0, mu, (n_new * new_mu + n_past * mu) / n_total)

        # Combine variance of old and new data.
        old_ssd = n_past * var
        new_ssd = n_new * new_var
        total_ssd = old_ssd + new_ssd + (n_new * n_past / n_total) * (mu - new_mu) ** 2
        total_var = jnp.where(n_new == 0.0, var, total_ssd / n_total)
        # correct precision problem
        total_var = jnp.where(total_var < 0, 0., total_var)

        return total_mu, total_var

    def _update_theta_var(self, X, y):
        """Actual implementation of Gaussian NB fitting.
        
        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Training vectors, where `n_samples` is the number of samples and
            `n_features` is the number of features.

        y : array-like of shape (n_samples,)
            Target values.

        Returns
        -------
        self : object
        """
        classes = self.classes_
        n_features = self.n_features
        y_ = jnp.expand_dims(y, 1).repeat(n_features, 1)
        for i, y_i in enumerate(classes):
            X_i = jnp.where(y_ == y_i, X, 0.0)
            Indicate_i = jnp.where(y == y_i, 1, 0)
            N_i = jnp.sum(Indicate_i, axis=0)

            new_theta, new_sigma = self._update_mean_variance(
                self.class_count_[i], self.theta_[i, :], self.var_[i, :], X_i, N_i
            )

            self.class_count_ = self.class_count_.at[i].set(self.class_count_[i] + N_i)

            for j in range(n_features):
                self.theta_ = self.theta_.at[i, j].set(new_theta[j])
                self.var_ = self.var_.at[i, j].set(new_sigma[j])

        self.var_ = self.var_ + self.epsilon_
        self.class_prior_ = self.class_count_ / jnp.sum(self.class_count_)

        return self

    def _first_partial_fit(self, X, y):
        """The first time when classifier is called.
        """
        n_features = X.shape[1]
        n_classes = len(self.classes_)
        self.n_features = n_features
        self.theta_ = jnp.zeros((n_classes, n_features))
        self.var_ = jnp.zeros((n_classes, n_features))
        self.class_count_ = jnp.zeros(n_classes, dtype=jnp.float32)
        self.class_prior_ = jnp.zeros(n_classes, dtype=jnp.float32)
        self.epsilon_ = self.var_smoothing * jnp.var(X, axis=0).max()

        return self._update_theta_var(X, y)

    def _partial_fit(self, X, y):
        """Actual implementation of Gaussian NB partial fitting.
        """
        self.var_ = self.var_ - self.epsilon_
        return self._update_theta_var(X, y)

    def _joint_log_likelihood(self, X):
        """Compute the log likehood.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Testing vectors.
            
        Returns
        -------
        jll: array-like of shape (n_samples, n_classes)
            The joint log likelihood of testing samples w.r.t all classes.
        """
        joint_log_likelihood = []
        for i in range(len(self.classes_)):
            jointi = jnp.where(
                self.class_prior_[i] != 0, jnp.log(self.class_prior_[i]), -jnp.inf
            )
            n_ij = -0.5 * jnp.sum(jnp.log(2.0 * jnp.pi * self.var_[i, :]))
            n_ij -= 0.5 * jnp.sum(((X - self.theta_[i, :]) ** 2) / self.var_[i, :], 1)
            joint_log_likelihood.append(jointi + n_ij)
        joint_log_likelihood = jnp.array(joint_log_likelihood).T
        return joint_log_likelihood

    def predict(self, X):
        """Predict attribute.

        Parameters
        ----------
        X : array-like of shape (n_samples, n_features)
            Testing vectors.
            
        Returns
        -------
        result: array-like of shape (n_samples,)
            The predicted classes of testing samples.
        """
        jll = self._joint_log_likelihood(X)
        return self.classes_[jnp.argmax(jll, axis=1)]
