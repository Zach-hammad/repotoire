"use client";

import { useState } from "react";
import { Star, ThumbsUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import { useAssetReviews, useSubmitReview } from "@/lib/marketplace-hooks";
import { Review } from "@/types/marketplace";

interface ReviewCardProps {
  review: Review;
}

function ReviewCard({ review }: ReviewCardProps) {
  const formatDate = (dateString: string) => {
    const date = new Date(dateString);
    return date.toLocaleDateString("en-US", {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  };

  return (
    <div className="card-elevated rounded-xl p-5">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-3">
          {review.user_avatar_url ? (
            <img
              src={review.user_avatar_url}
              alt={review.user_name}
              className="w-10 h-10 rounded-full"
            />
          ) : (
            <div className="w-10 h-10 rounded-full bg-muted flex items-center justify-center">
              <span className="text-sm font-medium text-muted-foreground">
                {review.user_name.charAt(0).toUpperCase()}
              </span>
            </div>
          )}
          <div>
            <p className="text-sm font-medium text-foreground">{review.user_name}</p>
            <p className="text-xs text-muted-foreground">
              {formatDate(review.created_at)}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {Array.from({ length: 5 }).map((_, i) => (
            <Star
              key={i}
              className={cn(
                "w-4 h-4",
                i < review.rating
                  ? "fill-amber-500 text-amber-500"
                  : "text-muted-foreground"
              )}
            />
          ))}
        </div>
      </div>

      {review.comment && (
        <p className="text-sm text-muted-foreground mb-3">{review.comment}</p>
      )}

      <div className="flex items-center gap-2">
        <button className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground transition-colors">
          <ThumbsUp className="w-3.5 h-3.5" />
          <span>Helpful ({review.helpful_count})</span>
        </button>
      </div>
    </div>
  );
}

interface RatingDistributionProps {
  distribution: Record<number, number>;
  total: number;
}

function RatingDistribution({ distribution, total }: RatingDistributionProps) {
  return (
    <div className="space-y-2">
      {[5, 4, 3, 2, 1].map((rating) => {
        const count = distribution[rating] || 0;
        const percentage = total > 0 ? (count / total) * 100 : 0;
        return (
          <div key={rating} className="flex items-center gap-2">
            <span className="text-xs text-muted-foreground w-3">{rating}</span>
            <Star className="w-3 h-3 text-amber-500" />
            <Progress value={percentage} className="h-2 flex-1" />
            <span className="text-xs text-muted-foreground w-8 text-right">
              {count}
            </span>
          </div>
        );
      })}
    </div>
  );
}

interface ReviewFormProps {
  onSubmit: (rating: number, comment?: string) => void;
  isSubmitting: boolean;
}

function ReviewForm({ onSubmit, isSubmitting }: ReviewFormProps) {
  const [rating, setRating] = useState(0);
  const [hoverRating, setHoverRating] = useState(0);
  const [comment, setComment] = useState("");

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    if (rating > 0) {
      onSubmit(rating, comment || undefined);
    }
  };

  return (
    <form onSubmit={handleSubmit} className="card-elevated rounded-xl p-5">
      <h4 className="text-sm font-medium text-foreground mb-4">Write a Review</h4>

      {/* Star Rating */}
      <div className="flex items-center gap-1 mb-4">
        {[1, 2, 3, 4, 5].map((star) => (
          <button
            key={star}
            type="button"
            onClick={() => setRating(star)}
            onMouseEnter={() => setHoverRating(star)}
            onMouseLeave={() => setHoverRating(0)}
            className="focus:outline-none"
          >
            <Star
              className={cn(
                "w-6 h-6 transition-colors",
                star <= (hoverRating || rating)
                  ? "fill-amber-500 text-amber-500"
                  : "text-muted-foreground hover:text-amber-400"
              )}
            />
          </button>
        ))}
        <span className="ml-2 text-sm text-muted-foreground">
          {rating > 0 ? `${rating} star${rating !== 1 ? "s" : ""}` : "Select rating"}
        </span>
      </div>

      {/* Comment */}
      <Textarea
        placeholder="Share your experience with this asset (optional)"
        value={comment}
        onChange={(e) => setComment(e.target.value)}
        className="mb-4 min-h-[100px]"
      />

      <Button
        type="submit"
        disabled={rating === 0 || isSubmitting}
        className="font-display font-medium"
      >
        {isSubmitting ? "Submitting..." : "Submit Review"}
      </Button>
    </form>
  );
}

interface ReviewsSectionProps {
  publisherSlug: string;
  assetSlug: string;
  ratingAvg?: number;
  ratingCount?: number;
  className?: string;
}

export function ReviewsSection({
  publisherSlug,
  assetSlug,
  ratingAvg = 0,
  ratingCount = 0,
  className,
}: ReviewsSectionProps) {
  const { data: reviewsData, isLoading, mutate } = useAssetReviews(
    publisherSlug,
    assetSlug
  );
  const { trigger: submitReview, isMutating } = useSubmitReview(
    publisherSlug,
    assetSlug
  );

  const handleSubmitReview = async (rating: number, comment?: string) => {
    try {
      await submitReview({ rating, comment });
      mutate(); // Refresh reviews
    } catch (error) {
      console.error("Failed to submit review:", error);
    }
  };

  if (isLoading) {
    return (
      <div className={cn("space-y-4", className)}>
        <h3 className="text-lg font-medium text-foreground">Reviews ({ratingCount})</h3>
        <div className="animate-pulse space-y-4">
          {[1, 2, 3].map((i) => (
            <div key={i} className="card-elevated rounded-xl p-5 h-32" />
          ))}
        </div>
      </div>
    );
  }

  const reviews = reviewsData?.reviews || [];
  const distribution = reviewsData?.rating_distribution || { 1: 0, 2: 0, 3: 0, 4: 0, 5: 0 };
  // Use the count from props (asset data) for consistency with header
  const total = ratingCount;

  return (
    <div className={cn("space-y-6", className)}>
      <h3 className="text-lg font-medium text-foreground">
        Reviews ({total})
      </h3>

      {/* Rating Distribution */}
      {total > 0 && (
        <div className="card-elevated rounded-xl p-5">
          <RatingDistribution distribution={distribution} total={total} />
        </div>
      )}

      {/* Review Form */}
      <ReviewForm onSubmit={handleSubmitReview} isSubmitting={isMutating} />

      {/* Reviews List */}
      <div className="space-y-4">
        {reviews.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-8">
            No reviews yet. Be the first to review this asset!
          </p>
        ) : (
          reviews.map((review) => <ReviewCard key={review.id} review={review} />)
        )}
      </div>

      {/* Load More */}
      {reviewsData?.has_more && (
        <Button variant="outline" className="w-full font-display font-medium">
          Load More Reviews
        </Button>
      )}
    </div>
  );
}
