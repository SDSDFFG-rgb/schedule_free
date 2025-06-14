import torch
import torch.optim
import math

class AdamWScheduleFree(torch.optim.Optimizer):
    r"""
    Schedule-Free AdamW
    As the name suggests, no scheduler is needed with this optimizer. 
    To add warmup, rather than using a learning rate schedule you can just
    set the warmup_steps parameter.
    
    This optimizer requires that .train() and .val() be called before the
    beginning of training and evaluation respectively.
    
    Arguments:
        params (iterable): 
            Iterable of parameters to optimize or dicts defining 
            parameter groups.
        lr (float): 
            Learning rate parameter (default 1e-3)
        betas (Tuple[float, float], optional): coefficients used for computing
            running averages of gradient and its square (default: (0.9, 0.999)).
        eps (float): 
            Term added to the denominator outside of the root operation to 
            improve numerical stability. (default: 1e-8).
        weight_decay (float): 
            Weight decay, i.e. a L2 penalty (default: 0).
        warmup_steps (int): Enables a linear learning rate warmup (default 0).
        r (float): Use polynomial weighting in the average 
            with power r (default 0).
        weight_lr_power (float): During warmup, the weights in the average will
            be equal to lr raised to this power. Set to 0 for no weighting
            (default 2.0).
    """
    def __init__(self,
                 params, 
                 lr=0.0025, 
                 betas=(0.9, 0.999), 
                 eps=1e-8,
                 weight_decay=0,
                 warmup_steps=0,
                 r=0.0,
                 weight_lr_power=2.0,
                 cautious=False,  # Cautious option
                 ):

        defaults = dict(lr=lr, 
                        betas=betas, 
                        eps=eps,
                        r=r,
                        k=0,
                        warmup_steps=warmup_steps,
                        train_mode = True,
                        weight_sum=0.0,
                        lr_max=-1.0,
                        weight_lr_power=weight_lr_power,
                        weight_decay=weight_decay,
                        cautious=cautious) # Cautious option
        super().__init__(params, defaults)
    
    def eval(self):
        for group in self.param_groups:
            train_mode = group['train_mode']
            beta1, _ = group['betas']
            if train_mode:
                for p in group['params']:
                    state = self.state[p]
                    if 'z' in state:
                        # Set p.data to x
                        p.data.lerp_(end=state['z'], weight=1-1/beta1)
                group['train_mode'] = False

    def train(self):
        for group in self.param_groups:
            train_mode = group['train_mode']
            beta1, _ = group['betas']
            if not train_mode:
                for p in group['params']:
                    state = self.state[p]
                    if 'z' in state:
                        # Set p.data to y
                        p.data.lerp_(end=state['z'], weight=1-beta1)
                group['train_mode'] = True

    def step(self, closure=None):
        """Performs a single optimization step.

        Arguments:
            closure (callable, optional): A closure that reevaluates the model
                and returns the loss.
        """

        loss = None
        if closure is not None:
            loss = closure()
        
        for group in self.param_groups:
            eps = group['eps']
            beta1, beta2 = group['betas']
            decay = group['weight_decay']
            k = group['k']
            r = group['r']
            warmup_steps = group['warmup_steps']
            weight_lr_power = group['weight_lr_power']
            cautious = group['cautious'] # Cautious option

            if k < warmup_steps:
              sched = (k+1) / warmup_steps
            else:
              sched = 1.0
            
            bias_correction2 = 1 - beta2 ** (k+1)
            lr = group['lr']*sched*math.sqrt(bias_correction2)
            
            lr_max = group['lr_max'] = max(lr, group['lr_max'])
            
            weight = ((k+1)**r) * (lr_max**weight_lr_power)
            weight_sum = group['weight_sum'] = group['weight_sum'] + weight

            ckp1 = weight/weight_sum

            if not group['train_mode']:
                raise Exception("Not in train mode!")

            for p in group['params']:
                if p.grad is None:
                    continue

                y = p.data # Notation to match theory
                grad = p.grad.data

                state = self.state[p]

                if 'z' not in state:
                    state['z'] = torch.clone(y)
                    state['exp_avg_sq'] = torch.zeros_like(p.data)

                z = state['z']
                exp_avg_sq = state['exp_avg_sq']

                exp_avg_sq.mul_(beta2).addcmul_(grad, grad, value=1-beta2)
                denom = exp_avg_sq.sqrt().add_(eps)

                # Reuse grad buffer for memory efficiency
                grad_normalized = grad.div_(denom)

                # Weight decay calculated at y
                if decay != 0:
                    grad_normalized.add_(y, alpha=decay)

                if cautious:
                    u = (y - z).mul_(ckp1).add_(grad_normalized, alpha=lr*(beta1*(1-ckp1)-1))
                    mask = (u * grad > 0).to(grad.dtype)
                    mask.mul_(mask.numel() / (mask.sum() + 1))
                    u.mul_(mask)
                    y.sub_(u)
                else:
                    # These operations update y in-place,
                    # without computing x explicitly.
                    y.lerp_(end=z, weight=ckp1)
                    y.add_(grad_normalized, alpha=lr*(beta1*(1-ckp1)-1))

                # z step
                z.sub_(grad_normalized, alpha=lr)

            group['k'] = k+1
        return loss