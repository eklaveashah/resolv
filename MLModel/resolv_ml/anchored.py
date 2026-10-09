"""Regularized residual calibration: start at market odds, learn small corrections."""
import numpy as np
from scipy.optimize import minimize
from scipy.special import expit
from sklearn.base import BaseEstimator, ClassifierMixin

class MarketAnchoredLogit(ClassifierMixin, BaseEstimator):
    def __init__(self, penalty=5.0, max_adjustment=0.5):
        self.penalty=penalty
        self.max_adjustment=max_adjustment

    def fit(self, X, y):
        X=np.asarray(X,dtype=float); y=np.asarray(y,dtype=float)
        self.mean_=X.mean(axis=0)
        self.scale_=X.std(axis=0)
        self.scale_[self.scale_<1e-8]=1
        Z=np.column_stack([np.ones(len(X)),(X-self.mean_)/self.scale_])
        offset=X[:,0]
        def loss(w):
            eta=offset+Z@w
            val=np.mean(np.logaddexp(0,eta)-y*eta)+self.penalty*np.sum(w*w)/2
            grad=Z.T@(expit(eta)-y)/len(y)+self.penalty*w
            return val,grad
        fit=minimize(loss,np.zeros(Z.shape[1]),jac=True,method='L-BFGS-B')
        if not fit.success:
            raise ValueError('residual calibration did not converge')
        self.weights_=fit.x
        self.classes_=np.array([0,1])
        return self

    def predict_proba(self,X):
        X=np.asarray(X,dtype=float)
        Z=np.column_stack([np.ones(len(X)),(X-self.mean_)/self.scale_])
        correction=np.clip(Z@self.weights_,-self.max_adjustment,self.max_adjustment)
        p=expit(X[:,0]+correction)
        return np.column_stack([1-p,p])
